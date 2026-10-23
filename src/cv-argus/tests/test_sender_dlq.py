"""Live first, dead-letter on failure (protocol v2, contract C1.6): the buffer's outbox/DLQ/archive
states, the critical-only, newest-first round-robin DLQ, attempt counting, the `TAKEN` lease,
the v1 -> v2 migration, the server applying what the ESP32 reports, the orchestrator's severity
transitions, and the two Pi commands (`sender.inject`, `buffer.status`)."""

import json
import sqlite3
import time

import pytest

from cv_argus import constants
from cv_argus.alerts import Alert, AlertKind
from cv_argus.buffer import open_buffer
from cv_argus.buffer import status as buffer_status
from cv_argus.buffer.store import event_kind, is_critical_event
from cv_argus.orchestrator import Orchestrator
from cv_argus.sender import FakeListener, FakeTransport, SenderServer, protocol
from cv_argus.sender import inject

LEASE_MS = int(constants.BUFFER_LEASE_SECONDS * 1000)


def _reading(severity, *, transition=True, previous="none", episode=None, reason="eyes_closed"):
    payload = {"severity": severity, "reason": reason,
               "class_name": "Drowsy" if severity in ("medium", "critical") else "Not Drowsy",
               "probabilities": [0.05, 0.95], "transition": transition,
               "previous_severity": previous}
    if episode:
        payload["episode"] = episode
    return Alert.new(AlertKind.DROWSINESS, level=2, source_id="cam0", payload=payload)


def _critical(**kw):
    return _reading("critical", **kw)


def _medium(**kw):
    return _reading("medium", **kw)


def _ok(**kw):
    kw.setdefault("previous", "critical")
    return _reading("none", reason="ok", **kw)


def _keepalive():
    return _reading("critical", transition=False, previous="critical")


@pytest.fixture
def buf(tmp_path):
    b = open_buffer(tmp_path / "q.sqlite3")
    yield b
    b.close()


def _archive(buf):
    return {r[0]: r[1] for r in buf._conn.execute("SELECT id, archive_reason FROM archive")}


def _dead_letter(buf, alert, reason="no_network", body=None):
    """Enqueue `alert` and fail it once live (attempt 1)."""
    buf.enqueue(alert)
    assert buf.apply_fail(alert.id, reason, body) == "dlq"


def _replay_fail(buf, alert_id, reason="no_network", body=None):
    """A failed DLQ replay as the ESP32 does it: the row was served and `TAKEN` (leased), then
    `FAIL`ed. A FAIL for a dead letter that isn't leased is a resend and is ignored (C1.6)."""
    assert buf.lease([alert_id]) == 1
    return buf.apply_fail(alert_id, reason, body)


# --- which rows are events (C1.5) -----------------------------------------------------------

@pytest.mark.parametrize("severity,previous,transition,expected", [
    ("medium", "none", True, "medium"),
    ("medium", "low", True, "medium"),
    ("medium", None, True, "medium"),
    ("critical", "none", True, "critical"),
    ("critical", "medium", True, "critical"),     # the escalation
    ("none", "critical", True, "ok"),
    ("low", "medium", True, "ok"),
    ("critical", "critical", False, None),        # keepalive
    ("medium", "critical", True, None),           # critical -> medium is not an event
    ("low", "none", True, None),
    ("none", "low", True, None),
    ("none", "none", True, None),                 # class-only change
])
def test_event_rule(severity, previous, transition, expected):
    payload = {"severity": severity, "previous_severity": previous, "transition": transition}
    assert event_kind(payload) == expected
    alert = Alert.new(AlertKind.DROWSINESS, level=2, source_id="c", payload=payload)
    assert is_critical_event(alert) == (expected == "critical")


def test_sql_and_python_event_rules_agree(buf):
    combos = [(s, p, t) for s in ("none", "low", "medium", "critical")
              for p in (None, "none", "low", "medium", "critical") for t in (True, False)]
    alerts = []
    for s, p, t in combos:
        a = Alert.new(AlertKind.DROWSINESS, level=1, source_id="c",
                      payload={"severity": s, "previous_severity": p, "transition": t})
        buf.enqueue(a)
        alerts.append(a)
    from cv_argus.buffer import store
    sql_events = {r[0] for r in buf._conn.execute(f"SELECT id FROM alerts WHERE {store._EVENT}")}
    sql_critical = {r[0] for r in buf._conn.execute(
        f"SELECT id FROM alerts WHERE {store._CRITICAL_EVENT}")}
    assert sql_events == {a.id for a in alerts if event_kind(a.payload)}
    assert sql_critical == {a.id for a in alerts if is_critical_event(a)}


# --- buffer: FAIL rules (C1.6) --------------------------------------------------------------

class TestFail:
    def test_new_rows_are_live_until_the_esp32_reports(self, buf):
        a = _critical()
        buf.enqueue(a)
        assert [x.id for x in buf.fetch_unsent()] == [a.id]
        assert buf.counts()["outbox"] == 1 and buf.counts()["dlq"] == 0
        assert buf.waiting() == (0, 1)

    def test_a_failed_live_critical_is_attempt_1_in_the_dlq(self, buf):
        a = _critical()
        buf.enqueue(a)
        body = {"severity": "critical", "position": {"lat": 20.6, "lon": -103.3}}
        assert buf.apply_fail(a.id, "no_network", body, now_ms=1_000_000) == "dlq"
        assert buf.fetch_unsent() == []
        [(alert, dlq)] = buf.fetch_dlq(10, now_ms=1_037_500)
        assert alert.id == a.id
        assert dlq == {"error": "no_network", "body": body, "failed_at_ms": 1_000_000,
                       "attempts": 1, "queued_s": 37}
        assert buf.counts()["dlq_reasons"] == {"no_network": 1}

    @pytest.mark.parametrize("make", [_medium, _ok], ids=["medium", "driver_ok"])
    def test_a_failed_medium_or_ok_is_archived_at_once(self, buf, make):
        a = make()
        buf.enqueue(a)
        assert buf.apply_fail(a.id, "retry_window") == "archived"
        assert buf.fetch_unsent() == [] and buf.fetch_dlq(10) == []
        assert _archive(buf) == {a.id: "retry_window"}

    @pytest.mark.parametrize("reason", constants.BUFFER_ARCHIVE_NOW_REASONS)
    def test_a_rejected_critical_is_archived_at_once(self, buf, reason):
        a = _critical()
        buf.enqueue(a)
        assert buf.apply_fail(a.id, reason) == "archived"
        assert _archive(buf) == {a.id: reason}
        assert buf.unsent_count() == 0

    def test_a_rejected_dead_letter_is_archived_at_once(self, buf):
        a = _critical()
        _dead_letter(buf, a)
        assert buf.apply_fail(a.id, "route_finished") == "archived"
        assert _archive(buf) == {a.id: "route_finished"}

    def test_the_first_composed_event_is_kept(self, buf):
        a = _critical()
        buf.enqueue(a)
        buf.apply_fail(a.id, "no_network", {"original": True})
        _replay_fail(buf, a.id, "http_503", {"later": True})
        _replay_fail(buf, a.id, "timeout", None)
        [(_, dlq)] = buf.fetch_dlq(10)
        assert dlq["body"] == {"original": True}
        assert dlq["error"] == "timeout" and dlq["attempts"] == 3

    def test_a_body_arrives_later_when_the_first_failure_had_none(self, buf):
        a = _critical()
        buf.enqueue(a)
        buf.expire_outbox(int(time.time() * 1000) + 1000)     # esp32_unreachable, no body
        _replay_fail(buf, a.id, "no_network", {"composed": True})
        [(_, dlq)] = buf.fetch_dlq(10)
        assert dlq["body"] == {"composed": True} and dlq["attempts"] == 1

    def test_every_attempt_counts_and_5_archive_it(self, buf):
        a = _critical()
        buf.enqueue(a)
        assert buf.apply_fail(a.id, "no_network") == "dlq"     # the live failure: attempt 1
        for attempt in range(2, constants.BUFFER_DLQ_MAX_ATTEMPTS):
            [(_, dlq)] = buf.fetch_dlq(10)
            assert dlq["attempts"] == attempt - 1
            assert _replay_fail(buf, a.id, "no_network") == "dlq"
        assert _replay_fail(buf, a.id, "timeout") == "archived"
        assert _archive(buf) == {a.id: "max_attempts"}
        row = buf._conn.execute(
            "SELECT attempts, last_error, archived_at_ms FROM archive WHERE id = ?", (a.id,)
        ).fetchone()
        assert row[0] == 5 and row[1] == "timeout" and row[2] > 0

    def test_a_preempted_replay_goes_back_without_an_attempt(self, buf):
        a = _critical()
        _dead_letter(buf, a)
        [(served, _)] = buf.serve_dlq(1)
        buf.lease([served.id])
        round_before = buf._conn.execute(
            "SELECT dlq_round FROM alerts WHERE id = ?", (a.id,)).fetchone()[0]
        assert buf.apply_fail(a.id, "preempted") == "requeued"
        row = buf._conn.execute(
            "SELECT state, attempts, lease_until_ms, dlq_round FROM alerts WHERE id = ?", (a.id,)
        ).fetchone()
        assert row == ("dlq", 1, None, round_before)
        assert [x.id for x, _ in buf.fetch_dlq(1)] == [a.id]

    def test_fail_for_unknown_sent_or_archived_ids_is_ignored(self, buf):
        sent, archived = _critical(), _medium()
        for a in (sent, archived):
            buf.enqueue(a)
        buf.mark_sent([sent.id])
        buf.apply_fail(archived.id, "retry_window")
        assert buf.apply_fail("nope", "no_network") is None
        assert buf.apply_fail(sent.id, "no_network") is None
        assert buf.apply_fail(archived.id, "no_network") is None
        assert buf.counts()["dlq"] == 0

    def test_ack_delivers_a_dead_letter_and_ignores_archived_ids(self, buf):
        a, gone = _critical(), _medium()
        _dead_letter(buf, a)
        buf.enqueue(gone)
        buf.apply_fail(gone.id, "retry_window")
        assert buf.mark_sent([a.id, gone.id]) == 1
        assert buf.fetch_dlq(10) == [] and buf.counts()["sent"] == 1

    # --- [sync 2] repeated-FAIL guard, retry_window and other reasons --------------------

    def _row(self, buf, alert_id):
        return buf._conn.execute(
            "SELECT state, attempts, lease_until_ms, dlq_round, dlq_body FROM alerts WHERE id = ?",
            (alert_id,),
        ).fetchone()

    def test_the_same_replay_fail_twice_counts_one_attempt(self, buf, caplog):
        a = _critical()
        _dead_letter(buf, a)                                   # attempt 1
        buf.serve_dlq(1)
        buf.lease([a.id])
        assert buf.apply_fail(a.id, "no_network") == "dlq"     # attempt 2, lease cleared
        with caplog.at_level("DEBUG", logger="cv_argus.buffer.store"):
            assert buf.apply_fail(a.id, "no_network") is None  # resent: ignored
        assert "ignoring repeated FAIL" in caplog.text
        [(_, dlq)] = buf.fetch_dlq(1)
        assert dlq["attempts"] == 2

    def test_an_outbox_critical_failed_twice_is_one_attempt_in_the_dlq(self, buf):
        a = _critical()
        buf.enqueue(a)
        buf.lease([a.id])
        body = {"first": True}
        assert buf.apply_fail(a.id, "no_network", body) == "dlq"
        assert buf.apply_fail(a.id, "no_network", body) is None
        state, attempts, lease, _, stored = self._row(buf, a.id)
        assert (state, attempts, lease) == ("dlq", 1, None)
        assert json.loads(stored) == body

    def test_an_expired_lease_makes_a_late_fail_an_uncounted_attempt(self, buf):
        # Accepted cost (C1.6): the replay's FAIL arrived after its 120 s lease lapsed.
        a = _critical()
        _dead_letter(buf, a)
        buf.lease([a.id], now_ms=1_000_000)
        assert buf.apply_fail(a.id, "no_network", now_ms=1_000_000 + LEASE_MS + 1) is None
        assert buf.fetch_dlq(1)[0][1]["attempts"] == 1

    @pytest.mark.parametrize("reason", ["too_old", "route_finished", "invalid"])
    def test_rule_3_still_archives_a_dead_letter_served_but_not_taken(self, buf, reason):
        a = _critical()
        _dead_letter(buf, a)
        assert buf.serve_dlq(1)[0][0].id == a.id               # served, never TAKEN
        assert buf.apply_fail(a.id, reason) == "archived"
        assert _archive(buf) == {a.id: reason}

    def test_rule_1_still_archives_an_unleased_non_critical(self, buf):
        # A non-critical never sits in the DLQ after the v2 migration; place one there directly
        # (a hand-edited or half-migrated file) to show rule 1 needs no lease.
        m = _medium()
        buf.enqueue(m)
        buf._conn.execute("UPDATE alerts SET state = 'dlq' WHERE id = ?", (m.id,))
        assert buf.apply_fail(m.id, "no_network") == "archived"
        assert _archive(buf) == {m.id: "no_network"}

    def test_a_preempted_live_critical_stays_in_the_outbox(self, buf):
        # [sync 2] rule 2 on a live row: lease cleared, attempts and round unchanged.
        a = _critical()
        buf.enqueue(a)
        buf.lease([a.id])
        assert buf.apply_fail(a.id, "preempted") == "requeued"
        assert self._row(buf, a.id)[:4] == ("outbox", 0, None, 0)
        assert [x.id for x in buf.fetch_unsent()] == [a.id]
        assert buf.waiting() == (0, 1)

    def test_a_critical_failed_with_retry_window_is_rule_4(self, buf):
        a = _critical()
        buf.enqueue(a)
        buf.lease([a.id])
        body = {"severity": "critical", "position": None}
        assert buf.apply_fail(a.id, "retry_window", body) == "dlq"
        [(_, dlq)] = buf.fetch_dlq(1)
        assert dlq["attempts"] == 1 and dlq["body"] == body and dlq["error"] == "retry_window"
        assert _archive(buf) == {}

    @pytest.mark.parametrize("make", [_medium, _ok], ids=["medium", "driver_ok"])
    def test_a_medium_or_ok_failed_with_retry_window_is_archived(self, buf, make):
        a = make()
        buf.enqueue(a)
        buf.lease([a.id])
        assert buf.apply_fail(a.id, "retry_window") == "archived"
        assert _archive(buf) == {a.id: "retry_window"}

    @pytest.mark.parametrize("reason", ["bad_response", "http_503", "http_500", "no_credentials",
                                        "transport_error", "something_new"])
    def test_other_reasons_are_rule_4(self, buf, reason):
        a = _critical()
        buf.enqueue(a)
        assert buf.apply_fail(a.id, reason) == "dlq"
        [(_, dlq)] = buf.fetch_dlq(1)
        assert dlq["attempts"] == 1 and dlq["error"] == reason


# --- buffer: DLQ order (newest first, round-robin) -------------------------------------------

class TestDlqOrder:
    def _three(self, buf):
        rows = [_critical() for _ in range(3)]
        for a in rows:
            _dead_letter(buf, a)
        return rows

    def test_newest_first(self, buf):
        old, mid, new = self._three(buf)
        assert [a.id for a, _ in buf.fetch_dlq(10)] == [new.id, mid.id, old.id]

    def test_round_robin_one_row_per_serve(self, buf):
        old, mid, new = self._three(buf)
        served = [buf.serve_dlq(1)[0][0].id for _ in range(6)]
        assert served == [new.id, mid.id, old.id] * 2

    def test_fetch_dlq_is_read_only(self, buf):
        _, _, new = self._three(buf)
        for _ in range(3):
            assert buf.fetch_dlq(1)[0][0].id == new.id

    def test_a_new_dead_letter_joins_the_current_round_ahead_of_older_rows(self, buf):
        old, mid, new = self._three(buf)
        assert buf.serve_dlq(1)[0][0].id == new.id      # round 0 -> new is now round 1
        newer = _critical()
        _dead_letter(buf, newer)                         # joins round 0 (the current minimum)
        served = [buf.serve_dlq(1)[0][0].id for _ in range(5)]
        # Round 0: newer, mid, old. Round 1: everyone, newest first again.
        assert served == [newer.id, mid.id, old.id, newer.id, new.id]

    def test_a_failed_replay_keeps_its_round(self, buf):
        old, mid, new = self._three(buf)
        [(a, _)] = buf.serve_dlq(1)
        assert _replay_fail(buf, a.id, "no_network") == "dlq"
        assert buf.serve_dlq(1)[0][0].id == mid.id      # new can't take two attempts in a row

    def test_only_one_row_per_dlq_request(self):
        assert protocol.MAX_DLQ_LIMIT == 1
        assert protocol.parse_dlq("DLQ 5") == 1


# --- buffer: the TAKEN lease ------------------------------------------------------------------

class TestLease:
    def test_a_leased_row_is_hidden_until_the_lease_expires(self, buf):
        a = _critical()
        buf.enqueue(a)
        assert buf.lease([a.id], now_ms=1_000_000) == 1
        assert buf.fetch_unsent(now_ms=1_000_000 + LEASE_MS - 1) == []
        assert buf.waiting(now_ms=1_000_000 + 1) == (0, 0)
        assert buf.counts(now_ms=1_000_001)["leased"] == 1
        assert [x.id for x in buf.fetch_unsent(now_ms=1_000_000 + LEASE_MS)] == [a.id]

    def test_an_expired_lease_returns_a_dead_letter_to_the_dlq_without_an_attempt(self, buf):
        a = _critical()
        _dead_letter(buf, a)
        buf.lease([a.id], now_ms=1_000_000)
        assert buf.fetch_dlq(1, now_ms=1_000_000 + 10) == []
        [(back, dlq)] = buf.fetch_dlq(1, now_ms=1_000_000 + LEASE_MS + 1)
        assert back.id == a.id and dlq["attempts"] == 1
        assert buf.counts()["outbox"] == 0

    def test_expire_outbox_skips_leased_rows(self, buf):
        a = _critical()
        buf.enqueue(a)
        now = int(time.time() * 1000)
        buf.lease([a.id], now_ms=now)
        assert buf.expire_outbox(now + 10_000, now_ms=now + 1) == (0, 0, 0)
        assert buf.counts()["outbox"] == 1

    def test_ack_and_fail_clear_the_lease(self, buf):
        a, b = _critical(), _critical()
        for x in (a, b):
            buf.enqueue(x)
        buf.lease([a.id, b.id])
        assert buf.mark_sent([a.id]) == 1
        assert buf.apply_fail(b.id, "no_network") == "dlq"
        lease = buf._conn.execute(
            "SELECT lease_until_ms FROM alerts WHERE id = ?", (b.id,)).fetchone()[0]
        assert lease is None and [x.id for x, _ in buf.fetch_dlq(1)] == [b.id]

    def test_lease_only_touches_outbox_and_dlq_rows(self, buf):
        a = _critical()
        buf.enqueue(a)
        buf.mark_sent([a.id])
        assert buf.lease([a.id, "nope"]) == 0
        assert buf.lease([]) == 0


# --- buffer: outbox expiry (C1.6) -------------------------------------------------------------

def test_expire_outbox_sorts_rows_by_what_they_are(buf):
    critical, escalation, medium, ok = _critical(), _critical(previous="medium"), _medium(), _ok()
    keepalive = _keepalive()
    de_escalation = _reading("medium", previous="critical")
    for a in (critical, escalation, medium, ok, keepalive, de_escalation):
        buf.enqueue(a)
    moved, archived, dropped = buf.expire_outbox(int(time.time() * 1000) + 1000)
    assert (moved, archived, dropped) == (2, 2, 2)
    letters = buf.fetch_dlq(10)
    assert {a.id for a, _ in letters} == {critical.id, escalation.id}
    assert all(d["error"] == "esp32_unreachable" and d["body"] is None and d["attempts"] == 0
               for _, d in letters)
    assert _archive(buf) == {medium.id: "esp32_unreachable", ok.id: "esp32_unreachable"}
    assert buf.counts()["outbox"] == 0


def test_expire_outbox_leaves_fresh_rows_alone(buf):
    buf.enqueue(_critical())
    assert buf.expire_outbox(int(time.time() * 1000) - 60_000) == (0, 0, 0)
    assert buf.counts()["outbox"] == 1


# --- buffer: migration of a v1 file (C1.6) -----------------------------------------------------

_V1_SCHEMA = """
CREATE TABLE alerts (id TEXT PRIMARY KEY, kind TEXT NOT NULL, level INTEGER,
  created_at_ms INTEGER NOT NULL, source_id TEXT NOT NULL, payload_json TEXT NOT NULL,
  geolocation_json TEXT, sent INTEGER NOT NULL DEFAULT 0, sent_at_ms INTEGER,
  enqueued_at_ms INTEGER NOT NULL, mono_ms INTEGER, boot_id TEXT, clock_synced INTEGER,
  state TEXT, dlq_error TEXT, dlq_body TEXT, dlq_at_ms INTEGER,
  attempts INTEGER NOT NULL DEFAULT 0);
"""


def _payload(severity, previous="none", transition=True):
    return json.dumps({"severity": severity, "previous_severity": previous,
                       "transition": transition, "class_name": "Drowsy"})


def _v1_file(path):
    conn = sqlite3.connect(path)
    conn.executescript(_V1_SCHEMA)
    rows = [
        # id, kind, payload, sent, state, attempts
        ("hb_out", "route_status", '{"status":"OK"}', 0, "outbox", 0),
        ("hb_dlq", "route_status", '{"status":"OK"}', 0, "dlq", 2),
        ("hb_sent", "route_status", '{"status":"OK"}', 1, "sent", 0),
        ("med_dlq", "drowsiness", _payload("medium"), 0, "dlq", 1),
        ("ok_dlq", "drowsiness", _payload("none", "critical"), 0, "dlq", 1),
        ("crit_dlq", "drowsiness", _payload("critical"), 0, "dlq", 2),
        ("crit_full", "drowsiness", _payload("critical", "medium"), 0, "dlq", 7),
        ("med_out", "drowsiness", _payload("medium"), 0, "outbox", 0),
    ]
    for i, (rid, kind, payload, sent, state, attempts) in enumerate(rows):
        conn.execute(
            "INSERT INTO alerts (id, kind, level, created_at_ms, source_id, payload_json, sent,"
            " enqueued_at_ms, state, dlq_error, dlq_at_ms, attempts)"
            " VALUES (?, ?, NULL, ?, 'cam0', ?, ?, ?, ?, ?, ?, ?)",
            (rid, kind, i, payload, sent, i, state,
             "no_network" if state == "dlq" else None, i if state == "dlq" else None, attempts),
        )
    conn.commit()
    conn.close()


def test_migration_moves_heartbeats_and_non_critical_dead_letters_to_the_archive(tmp_path):
    path = tmp_path / "v1.sqlite3"
    _v1_file(path)
    b = open_buffer(path)
    try:
        assert _archive(b) == {"hb_out": "migrated_v2", "hb_dlq": "migrated_v2",
                               "med_dlq": "migrated_v2", "ok_dlq": "migrated_v2",
                               "crit_full": "max_attempts"}
        [(crit, dlq)] = b.fetch_dlq(10)
        assert crit.id == "crit_dlq" and dlq["attempts"] == 2
        assert [a.id for a in b.fetch_unsent()] == ["med_out"]    # live rows stay live
        assert b.counts()["sent"] == 1                              # a sent heartbeat is left
        archived = b._conn.execute(
            "SELECT kind, attempts, dlq_error FROM archive WHERE id = 'hb_dlq'").fetchone()
        assert archived == ("route_status", 2, "no_network")        # every column is kept
    finally:
        b.close()


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "v1.sqlite3"
    _v1_file(path)
    open_buffer(path).close()
    b = open_buffer(path)
    try:
        assert len(_archive(b)) == 5 and b.counts()["dlq"] == 1
        cols = [r[1] for r in b._conn.execute("PRAGMA table_info(alerts)")]
        assert cols.count("lease_until_ms") == 1 and "dlq_round" in cols
    finally:
        b.close()


def test_an_older_file_without_states_still_opens(tmp_path):
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE alerts (id TEXT PRIMARY KEY, kind TEXT NOT NULL, level INTEGER,
          created_at_ms INTEGER NOT NULL, source_id TEXT NOT NULL, payload_json TEXT NOT NULL,
          geolocation_json TEXT, sent INTEGER NOT NULL DEFAULT 0, sent_at_ms INTEGER,
          enqueued_at_ms INTEGER NOT NULL);
        INSERT INTO alerts VALUES ('a', 'route_status', NULL, 1, 'cam0', '{}', NULL, 0, NULL, 1);
        INSERT INTO alerts VALUES ('b', 'drowsiness', 2, 2, 'cam0', '{}', NULL, 0, NULL, 2);
    """)
    conn.commit()
    conn.close()
    b = open_buffer(path)
    try:
        assert [a.id for a in b.fetch_unsent()] == ["b"]
        assert _archive(b) == {"a": "migrated_v2"}
    finally:
        b.close()


# --- protocol ---------------------------------------------------------------------------------

class TestGrammar:
    def test_end_carries_both_waiting_counts(self):
        assert protocol.format_end(3, 7) == "END 3 7"
        assert protocol.parse_end("END 3 7") == (3, 7)
        assert protocol.parse_end("END 3 7\r") == (3, 7)
        assert protocol.parse_end("END") is None          # v1 shapes are gone
        assert protocol.parse_end("END 3") is None
        assert protocol.parse_end("ENDX 1 2") is None
        assert protocol.parse_end("END x 2") is None

    def test_dlq_request_is_clamped_to_one(self):
        assert protocol.parse_dlq("DLQ 1") == 1
        assert protocol.parse_dlq("DLQ 999") == 1
        assert protocol.parse_dlq("DLQ 0") == 0
        assert protocol.parse_dlq("DLQ") is None

    def test_fail_round_trip_with_spaces_in_the_body(self):
        body = {"alert_type": "eyes closed", "position": {"lat": 1.5, "lon": 2.5}}
        line = protocol.format_fail("abc", "no_network", body)
        assert protocol.parse_fail(line) == ("abc", "no_network", body)

    def test_fail_without_a_body(self):
        assert protocol.parse_fail("FAIL abc retry_window -") == ("abc", "retry_window", None)
        assert protocol.parse_fail("FAIL abc http_503") == ("abc", "http_503", None)
        assert protocol.parse_fail("FAIL abc no_gps {not json") == ("abc", "no_gps", None)

    def test_malformed_fail(self):
        assert protocol.parse_fail("FAIL") is None
        assert protocol.parse_fail("FAIL abc") is None
        assert protocol.parse_fail("FAIL abc bad/reason -") is None  # the reason is one token
        assert protocol.parse_fail("ACK abc") is None

    def test_taken_and_leased(self):
        assert protocol.parse_taken("TAKEN a,b") == ["a", "b"]
        assert protocol.parse_taken(protocol.format_taken(["x"])) == ["x"]
        assert protocol.parse_taken("TAKEN") is None
        assert protocol.parse_taken("TAKEN a,,b") is None
        assert protocol.format_leased(2) == "LEASED 2"

    def test_result_line(self):
        line = protocol.format_result(2, 1, 3, 7, 4)
        assert line == "OK acked=2 failed=1 leased=3 dlq=7 outbox=4"
        assert protocol.parse_result(line) == {"acked": 2, "failed": 1, "leased": 3, "dlq": 7,
                                               "outbox": 4}
        assert protocol.parse_result("OK acked=1 failed=0 dlq=0") is None


# --- server -----------------------------------------------------------------------------------

class _Session:
    def __init__(self, buf, **kwargs):
        self.listener = FakeListener()
        kwargs.setdefault("ack_timeout", 2.0)
        self.server = SenderServer(buf, self.listener, accept_timeout=0.05, **kwargs)
        self.server.start()

    def connect(self):
        client, server_side = FakeTransport.pair()
        self.listener.offer(server_side)
        return client

    def stop(self):
        self.server.stop()
        self.server.join(timeout=3.0)


def _send(client, line):
    client.write((line + "\n").encode())


def _recv(client):
    line = client.readline(timeout=2.0)
    assert line is not None, "server closed the connection"
    return line.decode()


def _read_batch(client):
    rows = []
    while True:
        line = _recv(client)
        end = protocol.parse_end(line)
        if end is not None:
            return rows, end
        rows.append(json.loads(line))


def _done(client):
    _send(client, "DONE")
    return protocol.parse_result(_recv(client))


@pytest.fixture
def session(buf):
    s = _Session(buf)
    yield s
    s.stop()


class TestServer:
    def test_pull_serves_records_with_age_then_end_counts(self, buf, session):
        a, b = _critical(), _medium()
        buf.enqueue(a)
        buf.enqueue(b)
        _dead_letter(buf, _critical())
        client = session.connect()
        _send(client, "PULL 10")
        rows, (dlq, outbox) = _read_batch(client)
        assert [r["id"] for r in rows] == [a.id, b.id]
        assert (dlq, outbox) == (1, 2)          # served rows aren't leased yet: still counted
        assert all(isinstance(r["age_s"], float) and 0 <= r["age_s"] < 5 for r in rows)
        assert all("dlq" not in r for r in rows)
        assert _done(client) == {"acked": 0, "failed": 0, "leased": 0, "dlq": 1, "outbox": 2}

    def test_unknowable_age_is_null(self, buf, session):
        a = Alert(id="old", kind=AlertKind.DROWSINESS, level=2, created_at_ms=1, source_id="c",
                  payload={"severity": "critical", "transition": True}, mono_ms=5,
                  boot_id="another-boot", clock_synced=False)
        buf.enqueue(a)
        client = session.connect()
        _send(client, "PULL 1")
        [row], _ = _read_batch(client)
        assert row["age_s"] is None

    def test_taken_leases_served_rows_and_ack_fail_report_them_later(self, buf, session):
        keep, event, other = _keepalive(), _critical(), _medium()
        for a in (keep, event):
            buf.enqueue(a)
        client = session.connect()
        _send(client, "PULL 10")
        _read_batch(client)
        buf.enqueue(other)                                   # never served in this session
        _send(client, protocol.format_ack([keep.id]))
        assert _recv(client) == "ACKED 1"
        _send(client, protocol.format_taken([event.id, other.id]))
        assert _recv(client) == "LEASED 1"                   # only same-session ids
        assert _done(client) == {"acked": 1, "failed": 0, "leased": 1, "dlq": 0, "outbox": 1}
        assert [a.id for a in buf.fetch_unsent()] == [other.id]

        # A report session: the sender task's result for the leased row.
        later = session.connect()
        _send(later, protocol.format_ack([event.id]))
        assert _recv(later) == "ACKED 1"
        assert _done(later) == {"acked": 1, "failed": 0, "leased": 0, "dlq": 0, "outbox": 1}
        assert buf.counts()["sent"] == 2

    def test_a_report_session_can_open_with_a_standalone_fail(self, buf, session):
        a, b = _critical(), _medium()
        for x in (a, b):
            buf.enqueue(x)
        buf.lease([a.id, b.id])
        client = session.connect()
        _send(client, protocol.format_fail(a.id, "no_network", {"severity": "critical"}))
        _send(client, protocol.format_fail(b.id, "retry_window"))
        assert _done(client) == {"acked": 0, "failed": 2, "leased": 0, "dlq": 1, "outbox": 0}
        [(alert, dlq)] = buf.fetch_dlq(1)
        assert alert.id == a.id and dlq["attempts"] == 1 and dlq["body"] == {"severity": "critical"}
        assert _archive(buf) == {b.id: "retry_window"}

    def test_a_fail_for_a_row_not_in_this_batch_is_honoured(self, buf, session):
        a, other = _critical(), _critical()
        buf.enqueue(a)
        client = session.connect()
        _send(client, "PULL 10")
        _read_batch(client)
        buf.enqueue(other)
        _send(client, protocol.format_fail(other.id, "no_network"))
        assert _done(client)["failed"] == 1
        assert [x.id for x, _ in buf.fetch_dlq(1)] == [other.id]

    def test_a_taken_row_is_not_served_again_while_leased(self, buf, session):
        a = _critical()
        buf.enqueue(a)
        client = session.connect()
        _send(client, "PULL 10")
        _read_batch(client)
        _send(client, protocol.format_taken([a.id]))
        assert _recv(client) == "LEASED 1"
        _done(client)
        again = session.connect()
        _send(again, "PULL 10")
        rows, waiting = _read_batch(again)
        assert rows == [] and waiting == (0, 0)

    def test_dlq_serves_one_row_with_its_dlq_object(self, buf, session):
        old, new = _critical(), _critical()
        _dead_letter(buf, old, body={"position": {"lat": 1.0, "lon": 2.0}})
        _dead_letter(buf, new, body={"position": {"lat": 3.0, "lon": 4.0}})
        client = session.connect()
        _send(client, "DLQ 5")
        [row], (dlq, outbox) = _read_batch(client)
        assert row["id"] == new.id and (dlq, outbox) == (2, 0)
        assert set(row["dlq"]) == {"error", "body", "failed_at_ms", "attempts", "queued_s"}
        assert row["dlq"]["body"] == {"position": {"lat": 3.0, "lon": 4.0}}
        assert row["dlq"]["attempts"] == 1 and row["dlq"]["queued_s"] >= 0
        assert "age_s" in row
        _send(client, protocol.format_ack([new.id]))
        assert _recv(client) == "ACKED 1"
        assert _done(client) == {"acked": 1, "failed": 0, "leased": 0, "dlq": 1, "outbox": 0}

    def test_dlq_requests_go_round_robin(self, buf, session):
        old, new = _critical(), _critical()
        _dead_letter(buf, old)
        _dead_letter(buf, new)
        served = []
        for _ in range(3):
            client = session.connect()
            _send(client, "DLQ 1")
            [row], _ = _read_batch(client)
            served.append(row["id"])
            _done(client)
        assert served == [new.id, old.id, new.id]

    def test_a_failed_replay_is_one_more_attempt(self, buf, session):
        a = _critical()
        _dead_letter(buf, a, body={"keep": 1})
        client = session.connect()
        _send(client, "DLQ 1")
        _read_batch(client)
        _send(client, protocol.format_taken([a.id]))
        assert _recv(client) == "LEASED 1"
        _done(client)
        report = session.connect()
        _send(report, protocol.format_fail(a.id, "http_503"))
        _done(report)
        [(_, dlq)] = buf.fetch_dlq(1)
        assert dlq["error"] == "http_503" and dlq["attempts"] == 2 and dlq["body"] == {"keep": 1}

    def test_a_fail_resent_after_a_broken_session_is_not_counted(self, buf, session):
        # C1.2 [sync 2]: report lines take effect when read; the ESP32 resends outcomes it got
        # no OK for, and the repeated FAIL is ignored (not an attempt, not in `failed`).
        a = _critical()
        _dead_letter(buf, a, body={"keep": 1})
        client = session.connect()
        _send(client, "DLQ 1")
        _read_batch(client)
        _send(client, protocol.format_taken([a.id]))
        assert _recv(client) == "LEASED 1"
        _send(client, protocol.format_fail(a.id, "timeout"))
        client.close()                                   # broke before DONE -> no OK
        time.sleep(0.2)
        assert buf.fetch_dlq(1)[0][1]["attempts"] == 2   # applied when read
        report = session.connect()
        _send(report, protocol.format_fail(a.id, "timeout"))
        assert _done(report) == {"acked": 0, "failed": 0, "leased": 0, "dlq": 1, "outbox": 0}
        assert buf.fetch_dlq(1)[0][1]["attempts"] == 2

    def test_a_preempted_replay_is_not_an_attempt(self, buf, session):
        a = _critical()
        _dead_letter(buf, a)
        client = session.connect()
        _send(client, "DLQ 1")
        _read_batch(client)
        _send(client, protocol.format_fail(a.id, "preempted"))
        assert _done(client) == {"acked": 0, "failed": 1, "leased": 0, "dlq": 1, "outbox": 0}
        [(_, dlq)] = buf.fetch_dlq(1)
        assert dlq["attempts"] == 1

    def test_disconnect_before_reporting_changes_nothing(self, buf, session):
        a = _critical()
        buf.enqueue(a)
        client = session.connect()
        _send(client, "PULL 10")
        _read_batch(client)
        client.close()
        time.sleep(0.2)
        assert [x.id for x in buf.fetch_unsent()] == [a.id]
        assert buf.counts()["leased"] == 0

    def test_an_empty_pull_still_takes_report_lines(self, buf, session):
        a = _critical()
        buf.enqueue(a)
        buf.lease([a.id])
        client = session.connect()
        _send(client, "PULL 8")
        rows, waiting = _read_batch(client)
        assert rows == [] and waiting == (0, 0)
        _send(client, protocol.format_ack([a.id]))
        assert _recv(client) == "ACKED 1"
        assert _done(client)["acked"] == 1

    def test_a_new_connection_ends_a_session_the_esp32_abandoned(self, buf, session):
        # BT-20: the ESP32 resets after END and never reports; its next connection is served
        # within about a second instead of after the whole report wait.
        a = _critical()
        buf.enqueue(a)
        dead = session.connect()
        _send(dead, "PULL 10")
        _read_batch(dead)  # ... and then nothing, the link left half-open
        started = time.monotonic()
        fresh = session.connect()
        _send(fresh, "PULL 10")
        rows, _ = _read_batch(fresh)
        assert time.monotonic() - started < 5.0
        assert [r["id"] for r in rows] == [a.id]  # untouched: re-served

    def test_failures_and_the_queue_are_logged(self, buf, session, caplog):
        a, m = _critical(), _medium()
        buf.enqueue(a)
        buf.enqueue(m)
        client = session.connect()
        with caplog.at_level("INFO", logger="cv_argus.sender.server"):
            _send(client, "PULL 10")
            _read_batch(client)
            _send(client, protocol.format_fail(a.id, "no_network"))
            _send(client, protocol.format_fail(m.id, "retry_window"))
            _done(client)
            time.sleep(0.1)
        assert "couldn't deliver drowsiness critical (eyes_closed) -> dead-letter queue" in caplog.text
        assert "gave up on drowsiness medium (eyes_closed) -> archive (retry_window)" in caplog.text
        assert "dead-letter queue 1" in caplog.text and "archive 1" in caplog.text

    def test_housekeeping_expires_what_the_esp32_never_picked_up(self, buf, caplog):
        buf.enqueue(_critical())
        buf.enqueue(_medium())
        listener = FakeListener()
        server = SenderServer(buf, listener, accept_timeout=0.05, outbox_max_age_seconds=0.0)
        with caplog.at_level("WARNING", logger="cv_argus.sender.server"):
            server.start()
            deadline = time.monotonic() + 3.0
            while buf.counts()["dlq"] == 0 and time.monotonic() < deadline:
                time.sleep(0.05)
            server.stop()
            server.join(timeout=3.0)
        assert buf.counts()["dlq"] == 1 and buf.counts()["archived"] == 1
        assert "esp32_unreachable" in caplog.text

    def test_stop_ends_a_session_waiting_for_its_report(self, buf):
        s = _Session(buf, ack_timeout=30.0)
        client = s.connect()
        _send(client, "PULL 1")
        _read_batch(client)
        started = time.monotonic()
        s.stop()
        assert not s.server.is_alive and time.monotonic() - started < 3.0


# --- orchestrator: severity changes are transitions ------------------------------------------

class _FakeBuffer:
    def __init__(self):
        self.rows = []

    def enqueue(self, alert):
        self.rows.append(alert)


class _Probs(list):
    def tolist(self):
        return list(self)


class _Det:
    def __init__(self, severity):
        p = {"none": 0.05, "low": 0.3, "medium": 0.75, "critical": 0.95}[severity]
        self.level = 2 if severity in ("medium", "critical") else 1
        self.probabilities = _Probs([round(1 - p, 2), p])
        self.extras = {"severity": severity, "reason": "eyes_closed" if p > 0.5 else "ok"}


def _run(severities):
    buf = _FakeBuffer()
    orch = Orchestrator(buf, debounce_frames=3, keepalive_seconds=1e9, warmup_frames=0)
    for sev in severities:
        orch._on_detection("cam0", None if sev is None else _Det(sev))
    return [r for r in buf.rows if r.payload.get("transition")]


class TestSeverityTransitions:
    def test_medium_to_critical_is_its_own_transition_in_one_episode(self):
        rows = _run(["none"] * 4 + ["medium"] * 4 + ["critical"] * 4 + ["none"] * 4)
        changes = [(r.payload["previous_severity"], r.payload["severity"]) for r in rows
                   if r.payload["previous_severity"] != r.payload["severity"]]
        assert changes == [(None, "none"), ("none", "medium"), ("medium", "critical"),
                           ("critical", "none")]
        episodes = [r.payload.get("episode") for r in rows
                    if r.payload["severity"] in ("medium", "critical")]
        assert len(set(episodes)) == 1 and episodes[0]
        recovery = [r for r in rows if r.payload["previous_severity"] == "critical"][0]
        assert recovery.payload["episode"] == episodes[0]
        assert recovery.payload["class_name"] == "Not Drowsy"

    def test_a_new_incident_gets_a_new_episode(self):
        rows = _run(["none"] * 3 + ["critical"] * 3 + ["none"] * 3 + ["critical"] * 3)
        eps = {r.payload["episode"] for r in rows if r.payload["severity"] == "critical"}
        assert len(eps) == 2

    def test_a_severity_change_carries_this_frames_probabilities(self):
        rows = _run(["none"] * 4 + ["critical"])
        last = rows[-1]
        assert last.payload["severity"] == "critical"
        assert last.payload["probabilities"] == [0.05, 0.95]

    def test_a_face_less_severity_change_reports_no_face(self):
        # A severity change adopts this frame's class at once (C1.5): a frame with no detection
        # at all is No Face, which is the one class allowed to carry no probabilities.
        rows = _run(["none"] * 4 + ["critical"] * 4 + [None])
        last = rows[-1]
        assert last.payload["severity"] == "none" and last.payload["class_name"] == "No Face"
        assert last.level is None and last.payload["probabilities"] is None

    def test_no_face_frames_dont_repeat_transitions(self):
        rows = _run(["none"] * 4 + [None] * 30)
        assert len(rows) <= 3  # none, then No Face (class), nothing per frame after that


# --- the two Pi commands ---------------------------------------------------------------------

def test_inject_queues_a_transition_with_an_episode(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BUFFER_DIR", str(tmp_path))
    assert inject.main(["critical"]) == 0
    out = capsys.readouterr().out
    assert "episode=" in out and "inject none --episode" in out
    b = open_buffer()
    try:
        [row] = b.fetch_unsent()
    finally:
        b.close()
    assert row.payload["severity"] == "critical" and row.payload["transition"] is True
    assert row.payload["previous_severity"] == "none" and row.payload["episode"]
    assert row.source_id == "injected"
    assert event_kind(row.payload) == "critical"


def test_inject_resolve_uses_the_given_episode(tmp_path, monkeypatch):
    monkeypatch.setenv("BUFFER_DIR", str(tmp_path))
    inject.main(["none", "--episode", "ep1"])
    b = open_buffer()
    try:
        [row] = b.fetch_unsent()
    finally:
        b.close()
    assert row.payload["episode"] == "ep1" and row.payload["previous_severity"] == "critical"
    assert event_kind(row.payload) == "ok"


def test_status_lists_dead_letters_and_the_archive(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BUFFER_DIR", str(tmp_path))
    b = open_buffer()
    a, m = _critical(), _medium()
    b.enqueue(a)
    b.apply_fail(a.id, "no_network", {"severity": "critical"})
    b.enqueue(m)
    b.apply_fail(m.id, "retry_window")
    b.lease([a.id])
    b.close()
    assert buffer_status.main(["--bodies"]) == 0
    out = capsys.readouterr().out
    assert "dead-letter queue (critical only):    1" in out
    assert "held by the ESP32 (leased):           1" in out
    assert "archive (given up, never resent):     1" in out
    assert "retry_window 1" in out and m.id in out
    assert "no_network" in out and a.id in out and '"severity":"critical"' in out
    assert "1 attempt(s), round 0, leased" in out


def test_status_shows_a_migrated_heartbeat(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BUFFER_DIR", str(tmp_path))
    _v1_file(tmp_path / "buffer.sqlite3")
    assert buffer_status.main([]) == 0
    out = capsys.readouterr().out
    assert "route_status" in out and "migrated_v2" in out
