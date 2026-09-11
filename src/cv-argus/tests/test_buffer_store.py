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
