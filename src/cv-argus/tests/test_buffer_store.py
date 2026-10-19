"""`buffer/store.py` — schema, WAL mode, enqueue/fetch/mark_sent semantics, and a concurrency
smoke test. Always via `tmp_path` -- never a fixed path (this repo's hermetic-by-default rule).
"""

import threading

from cv_argus.alerts import Alert, AlertKind, route_status
from cv_argus.buffer import open_buffer


def _drowsy_alert(source_id="cam0"):
    return Alert.new(
        AlertKind.DROWSINESS,
        level=2,
        source_id=source_id,
        payload={"class_name": "Drowsy", "probabilities": [0.2, 0.8]},
    )


def test_open_buffer_creates_file_and_schema(tmp_path):
    buf = open_buffer(tmp_path / "b.sqlite3")
    assert (tmp_path / "b.sqlite3").exists()
    assert buf.unsent_count() == 0
    buf.close()


def test_journal_mode_is_wal(tmp_path):
    buf = open_buffer(tmp_path / "b.sqlite3")
    mode = buf._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    buf.close()


def test_enqueue_then_fetch_unsent_round_trips_a_drowsiness_alert(tmp_path):
    buf = open_buffer(tmp_path / "b.sqlite3")
    alert = _drowsy_alert()
    buf.enqueue(alert)
    fetched = buf.fetch_unsent()
    assert fetched == [alert]
    buf.close()


def test_enqueue_then_fetch_unsent_round_trips_a_route_status_alert(tmp_path):
    buf = open_buffer(tmp_path / "b.sqlite3")
    alert = route_status("cam0")
    buf.enqueue(alert)
    fetched = buf.fetch_unsent()
    assert fetched == [alert]
    buf.close()


def test_enqueue_is_idempotent_on_id(tmp_path):
    buf = open_buffer(tmp_path / "b.sqlite3")
    alert = _drowsy_alert()
    buf.enqueue(alert)
    buf.enqueue(alert)  # same id -- INSERT OR IGNORE
    assert buf.unsent_count() == 1
    buf.close()


def test_fetch_unsent_is_oldest_first(tmp_path):
    buf = open_buffer(tmp_path / "b.sqlite3")
    first = _drowsy_alert(source_id="first")
    second = _drowsy_alert(source_id="second")
    buf.enqueue(first)
    buf.enqueue(second)
    fetched = buf.fetch_unsent()
    assert [a.source_id for a in fetched] == ["first", "second"]
    buf.close()


def test_fetch_unsent_never_mutates_sent(tmp_path):
    buf = open_buffer(tmp_path / "b.sqlite3")
    buf.enqueue(_drowsy_alert())
    buf.fetch_unsent()
    buf.fetch_unsent()
    assert buf.unsent_count() == 1
    buf.close()


def test_mark_sent_flips_only_matching_unsent_rows(tmp_path):
    buf = open_buffer(tmp_path / "b.sqlite3")
    a, b, c = _drowsy_alert("a"), _drowsy_alert("b"), _drowsy_alert("c")
    for alert in (a, b, c):
        buf.enqueue(alert)

    updated = buf.mark_sent([a.id, b.id])

    assert updated == 2
    remaining_ids = {alert.id for alert in buf.fetch_unsent()}
    assert remaining_ids == {c.id}
    buf.close()


def test_mark_sent_on_already_sent_ids_returns_zero(tmp_path):
    buf = open_buffer(tmp_path / "b.sqlite3")
    alert = _drowsy_alert()
    buf.enqueue(alert)
    assert buf.mark_sent([alert.id]) == 1
    assert buf.mark_sent([alert.id]) == 0
    buf.close()


def test_mark_sent_with_empty_ids_is_a_noop(tmp_path):
    buf = open_buffer(tmp_path / "b.sqlite3")
    buf.enqueue(_drowsy_alert())
    assert buf.mark_sent([]) == 0
    assert buf.unsent_count() == 1
    buf.close()


def test_concurrent_enqueue_and_fetch_mark_sent_stays_consistent(tmp_path):
    """Exercises the WAL + threading.Lock claim, not just the pragma being set: one thread
    enqueues in a loop while another fetches/marks-sent in a loop against the same Buffer."""
    buf = open_buffer(tmp_path / "b.sqlite3")
    total_enqueued = 200
    stop = threading.Event()

    def writer():
        for i in range(total_enqueued):
            buf.enqueue(_drowsy_alert(source_id=f"w{i}"))
        stop.set()

    def reader():
        while not stop.is_set() or buf.unsent_count() > 0:
            rows = buf.fetch_unsent(limit=10)
            if rows:
                buf.mark_sent([r.id for r in rows])

    t_writer = threading.Thread(target=writer)
    t_reader = threading.Thread(target=reader)
    t_writer.start()
    t_reader.start()
    t_writer.join(timeout=10)
    t_reader.join(timeout=10)

    assert not t_writer.is_alive()
    assert not t_reader.is_alive()

    sent_count = buf._conn.execute("SELECT COUNT(*) FROM alerts WHERE sent = 1").fetchone()[0]
    assert buf.unsent_count() + sent_count == total_enqueued
    buf.close()


# --- Ordering, growth, restart, migration (CV-14, BUF-01..03) ------------------------------------

import sqlite3  # noqa: E402

import pytest  # noqa: E402

import cv_argus.buffer.store as store  # noqa: E402


def _reading(class_name="Drowsy", transition=True):
    return Alert.new(
        AlertKind.DROWSINESS,
        level=2 if class_name == "Drowsy" else 1,
        source_id="cam0",
        payload={"class_name": class_name, "probabilities": [0.2, 0.8], "transition": transition},
    )


def test_fetch_unsent_keeps_insertion_order_when_clock_goes_backwards(tmp_path, monkeypatch):
    buf = open_buffer(tmp_path / "b.sqlite3")
    first, second = _reading(), _reading()
    wall = iter([2_000_000.0, 1_000_000.0])  # NTP steps the clock back between two enqueues
    monkeypatch.setattr(store.time, "time", lambda: next(wall))
    buf.enqueue(first)
    buf.enqueue(second)
    assert [a.id for a in buf.fetch_unsent()] == [first.id, second.id]
    buf.close()


def test_prune_sent_removes_only_old_sent_rows(tmp_path):
    buf = open_buffer(tmp_path / "b.sqlite3")
    old_sent, new_sent, unsent = _reading(), _reading(), _reading()
    for a in (old_sent, new_sent, unsent):
        buf.enqueue(a)
    buf.mark_sent([old_sent.id, new_sent.id])
    buf._conn.execute("UPDATE alerts SET sent_at_ms = 1 WHERE id = ?", (old_sent.id,))

    removed = buf.prune_sent(older_than_ms=1_000)
    remaining = {row[0] for row in buf._conn.execute("SELECT id FROM alerts")}
    assert removed == 1
    assert remaining == {new_sent.id, unsent.id}
    buf.close()


def test_unsent_cap_drops_oldest_keepalives_never_transitions(tmp_path):
    buf = open_buffer(tmp_path / "b.sqlite3")
    transitions = [_reading("Drowsy", True), _reading("Not Drowsy", True)]
    keepalives = [_reading("Drowsy", False) for _ in range(10)]
    buf.enqueue(transitions[0])
    for k in keepalives:
        buf.enqueue(k)
    buf.enqueue(transitions[1])

    dropped = buf.cap_unsent(max_rows=5)
    kept = [a.id for a in buf.fetch_unsent(limit=100)]
    assert dropped == 7
    assert transitions[0].id in kept and transitions[1].id in kept
    assert kept[-2:] == [keepalives[-1].id, transitions[1].id]  # newest keepalives survive
    buf.close()


def test_restart_reopens_with_unsent_backlog_oldest_first(tmp_path):
    path = tmp_path / "b.sqlite3"
    buf = open_buffer(path)
    alerts = [_reading() for _ in range(5)]
    for a in alerts:
        buf.enqueue(a)
    buf.mark_sent([alerts[0].id])
    buf.close()

    reopened = open_buffer(path)
    assert [a.id for a in reopened.fetch_unsent()] == [a.id for a in alerts[1:]]
    reopened.close()


def test_clock_fields_survive_the_buffer(tmp_path):
    buf = open_buffer(tmp_path / "b.sqlite3")
    alert = _reading()
    buf.enqueue(alert)
    [back] = buf.fetch_unsent()
    assert (back.mono_ms, back.boot_id, back.clock_synced) == (
        alert.mono_ms,
        alert.boot_id,
        alert.clock_synced,
    )
    buf.close()


_OLD_SCHEMA = """
CREATE TABLE alerts (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, level INTEGER, created_at_ms INTEGER NOT NULL,
    source_id TEXT NOT NULL, payload_json TEXT NOT NULL, geolocation_json TEXT,
    sent INTEGER NOT NULL DEFAULT 0, sent_at_ms INTEGER, enqueued_at_ms INTEGER NOT NULL
);
"""


def test_buffer_file_from_previous_release_opens_and_serves_old_rows(tmp_path):
    path = tmp_path / "b.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(_OLD_SCHEMA)
    conn.execute(
        "INSERT INTO alerts (id, kind, level, created_at_ms, source_id, payload_json,"
        " enqueued_at_ms) VALUES ('old1', 'drowsiness', 2, 1, 'cam0', '{}', 1)"
    )
    conn.commit()
    conn.close()

    buf = open_buffer(path)
    [old] = buf.fetch_unsent()
    assert old.id == "old1" and old.mono_ms is None
    buf.enqueue(_reading())  # new rows still insert into the migrated table
    assert buf.unsent_count() == 2
    buf.close()


def test_readers_and_writer_threads_stay_consistent(tmp_path):
    """BUF-03: sender reads (fetch_unsent/unsent_count) while orchestrator writes."""
    buf = open_buffer(tmp_path / "b.sqlite3")
    errors = []

    def writer():
        try:
            for _ in range(200):
                buf.enqueue(_reading())
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    def reader():
        try:
            for _ in range(200):
                rows = buf.fetch_unsent(limit=10)
                buf.mark_sent([r.id for r in rows])
                buf.unsent_count()
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert errors == []
    while rows := buf.fetch_unsent(limit=50):
        buf.mark_sent([r.id for r in rows])
    assert buf.unsent_count() == 0
    buf.close()


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="BUF-06: cap_unsent only drops keepalives, so transitions/heartbeats grow forever")
def test_unsent_backlog_stays_bounded_without_keepalives(tmp_path):
    """A permanent outage (a rotated device key the ESP32 never got, a dead ESP32) means nothing
    is ever ACKed. Keepalives are dropped first, but once only transitions and heartbeats are
    left the unsent backlog still has to stay under the cap on the Pi's SD card, keeping the
    newest rows (they describe the present)."""
    buf = open_buffer(tmp_path / "b.sqlite3")
    rows = []
    for i in range(15):
        rows.append(_reading("Drowsy" if i % 2 else "Not Drowsy", transition=True))
        rows.append(route_status("cam0"))
    for row in rows:
        buf.enqueue(row)

    buf.cap_unsent(max_rows=10)
    kept = [a.id for a in buf.fetch_unsent(limit=100)]
    assert buf.unsent_count() <= 10, f"{buf.unsent_count()} unsent rows kept, cap is 10"
    assert kept[-1] == rows[-1].id
    buf.close()
