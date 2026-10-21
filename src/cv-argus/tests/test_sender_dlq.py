"""Live first, dead-letter on failure: the buffer's outbox/DLQ states, the FAIL/DLQ/DONE grammar,
the server applying what the ESP32 reports, the orchestrator's severity transitions, and the two
Pi commands (`sender.inject`, `buffer.status`)."""

import json
import sqlite3
import time

import pytest

from cv_argus.alerts import Alert, AlertKind, route_status
from cv_argus.buffer import open_buffer
from cv_argus.buffer import status as buffer_status
from cv_argus.orchestrator import Orchestrator
from cv_argus.sender import FakeListener, FakeTransport, SenderServer, protocol
from cv_argus.sender import inject


def _reading(severity, *, transition=True, previous="none", episode=None, reason="eyes_closed"):
    payload = {"severity": severity, "reason": reason, "class_name": "Drowsy",
               "probabilities": [0.05, 0.95], "transition": transition,
               "previous_severity": previous}
    if episode:
        payload["episode"] = episode
    return Alert.new(AlertKind.DROWSINESS, level=2, source_id="cam0", payload=payload)


@pytest.fixture
def buf(tmp_path):
    b = open_buffer(tmp_path / "q.sqlite3")
    yield b
    b.close()


# --- buffer ---------------------------------------------------------------------------------

class TestBufferStates:
    def test_new_rows_are_live_until_the_esp32_reports(self, buf):
        a = _reading("critical")
        buf.enqueue(a)
        assert [x.id for x in buf.fetch_unsent()] == [a.id]
        assert buf.counts()["outbox"] == 1 and buf.counts()["dlq"] == 0

    def test_fail_moves_to_dlq_with_reason_and_body(self, buf):
        a = _reading("critical")
        buf.enqueue(a)
        body = {"severity_level": "critical", "coordinates": {"lat": 20.6, "lon": -103.3}}
        assert buf.mark_dlq(a.id, "no_credentials", body)
        assert buf.fetch_unsent() == []
        [(alert, dlq)] = buf.fetch_dlq(10)
        assert alert.id == a.id
        assert dlq["error"] == "no_credentials" and dlq["body"] == body and dlq["attempts"] == 1
        assert buf.counts()["dlq_reasons"] == {"no_credentials": 1}

    def test_a_failed_replay_keeps_the_original_message(self, buf):
        a = _reading("critical")
        buf.enqueue(a)
        buf.mark_dlq(a.id, "no_credentials", {"original": True})
        buf.mark_dlq(a.id, "http_503", None)
        [(_, dlq)] = buf.fetch_dlq(10)
        assert dlq["body"] == {"original": True}
        assert dlq["error"] == "http_503" and dlq["attempts"] == 2

    def test_ack_delivers_a_dead_letter(self, buf):
        a = _reading("critical")
        buf.enqueue(a)
        buf.mark_dlq(a.id, "no_network")
        assert buf.mark_sent([a.id]) == 1
        assert buf.fetch_dlq(10) == [] and buf.counts()["sent"] == 1

    def test_dlq_is_oldest_first(self, buf):
        ids = []
        for _ in range(3):
            a = _reading("critical")
            buf.enqueue(a)
            ids.append(a.id)
        for i in reversed(ids):
            buf.mark_dlq(i, "no_credentials")
        assert [a.id for a, _ in buf.fetch_dlq(10)] == ids

    def test_expire_outbox_dead_letters_backend_bound_and_drops_keepalives(self, buf):
        transition = _reading("critical")
        keepalive = _reading("critical", transition=False)
        heartbeat = route_status("cam0")
        for a in (transition, keepalive, heartbeat):
            buf.enqueue(a)
        moved, dropped = buf.expire_outbox(int(time.time() * 1000) + 1000)
        assert (moved, dropped) == (2, 1)
        assert {a.id for a, d in buf.fetch_dlq(10)} == {transition.id, heartbeat.id}
        assert all(d["error"] == "esp32_unreachable" and d["body"] is None
                   for _, d in buf.fetch_dlq(10))

    def test_expire_outbox_leaves_fresh_rows_alone(self, buf):
        buf.enqueue(_reading("critical"))
        assert buf.expire_outbox(int(time.time() * 1000) - 60_000) == (0, 0)
        assert buf.counts()["outbox"] == 1

    def test_an_old_file_migrates_unsent_rows_to_the_outbox(self, tmp_path):
        path = tmp_path / "old.sqlite3"
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE alerts (id TEXT PRIMARY KEY, kind TEXT NOT NULL, level INTEGER,
              created_at_ms INTEGER NOT NULL, source_id TEXT NOT NULL, payload_json TEXT NOT NULL,
              geolocation_json TEXT, sent INTEGER NOT NULL DEFAULT 0, sent_at_ms INTEGER,
              enqueued_at_ms INTEGER NOT NULL);
            INSERT INTO alerts VALUES ('a', 'route_status', NULL, 1, 'cam0', '{}', NULL, 0, NULL, 1);
            INSERT INTO alerts VALUES ('b', 'route_status', NULL, 2, 'cam0', '{}', NULL, 1, 5, 2);
        """)
        conn.commit()
        conn.close()
        b = open_buffer(path)
        try:
            assert [a.id for a in b.fetch_unsent()] == ["a"]
            assert b.counts()["sent"] == 1
        finally:
            b.close()


# --- protocol -------------------------------------------------------------------------------

class TestGrammar:
    def test_end_with_and_without_a_count(self):
        assert protocol.format_end(3) == "END 3"
        assert protocol.parse_end("END 3") == 3
        assert protocol.parse_end("END") == 0
        assert protocol.parse_end("END\r") == 0
        assert protocol.parse_end("ENDX") is None
        assert protocol.parse_end("END x") is None

    def test_dlq_request_is_clamped(self):
        assert protocol.parse_dlq("DLQ 5") == 5
        assert protocol.parse_dlq("DLQ 999") == protocol.MAX_DLQ_LIMIT
        assert protocol.parse_dlq("DLQ") is None

    def test_fail_round_trip_with_spaces_in_the_body(self):
        body = {"alert_type": "eyes closed", "coordinates": {"lat": 1.5, "lon": 2.5}}
        line = protocol.format_fail("abc", "no_credentials", body)
        assert protocol.parse_fail(line) == ("abc", "no_credentials", body)

    def test_fail_without_a_body(self):
        assert protocol.parse_fail("FAIL abc no_gps -") == ("abc", "no_gps", None)
        assert protocol.parse_fail("FAIL abc no_gps") == ("abc", "no_gps", None)
        assert protocol.parse_fail("FAIL abc no_gps {not json") == ("abc", "no_gps", None)

    def test_malformed_fail(self):
        assert protocol.parse_fail("FAIL") is None
        assert protocol.parse_fail("FAIL abc") is None
        assert protocol.parse_fail("FAIL abc bad/reason -") is None  # the reason is one token
        assert protocol.parse_fail("ACK abc") is None

    def test_result_line(self):
        assert protocol.format_result(2, 1, 7) == "OK acked=2 failed=1 dlq=7"


# --- server ---------------------------------------------------------------------------------

class _Session:
    def __init__(self, buf):
        self.listener = FakeListener()
        self.server = SenderServer(buf, self.listener, accept_timeout=0.05, ack_timeout=2.0)
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


def _read_batch(client):
    rows = []
    while True:
        line = client.readline(timeout=2.0).decode()
        end = protocol.parse_end(line)
        if end is not None:
            return rows, end
        rows.append(json.loads(line))


@pytest.fixture
def session(buf):
    s = _Session(buf)
    yield s
    s.stop()


class TestServer:
    def test_live_fail_goes_to_the_dlq_and_ack_to_sent(self, buf, session):
        good, bad = _reading("critical"), _reading("medium")
        buf.enqueue(good)
        buf.enqueue(bad)
        client = session.connect()
        _send(client, "PULL 10")
        rows, waiting = _read_batch(client)
        assert [r["id"] for r in rows] == [good.id, bad.id] and waiting == 0

        _send(client, protocol.format_fail(bad.id, "no_credentials", {"severity_level": "medium"}))
        _send(client, protocol.format_ack([good.id]))
        assert client.readline(timeout=2.0).decode() == protocol.format_acked(1)
        _send(client, "DONE")
        assert client.readline(timeout=2.0).decode() == "OK acked=1 failed=1 dlq=1"

        assert buf.fetch_unsent() == []
        [(alert, dlq)] = buf.fetch_dlq(10)
        assert alert.id == bad.id and dlq["error"] == "no_credentials"
        assert dlq["body"] == {"severity_level": "medium"}

    def test_all_failed_needs_no_ack(self, buf, session):
        a = _reading("critical")
        buf.enqueue(a)
        client = session.connect()
        _send(client, "PULL 10")
        _read_batch(client)
        _send(client, protocol.format_fail(a.id, "no_network"))
        _send(client, "DONE")
        assert client.readline(timeout=2.0).decode() == "OK acked=0 failed=1 dlq=1"

    def test_end_reports_how_many_dead_letters_wait(self, buf, session):
        a = _reading("critical")
        buf.enqueue(a)
        buf.mark_dlq(a.id, "no_credentials")
        client = session.connect()
        _send(client, "PULL 10")
        rows, waiting = _read_batch(client)
        assert rows == [] and waiting == 1

    def test_dlq_replay_carries_the_stored_message_and_ack_delivers_it(self, buf, session):
        a = _reading("critical")
        buf.enqueue(a)
        buf.mark_dlq(a.id, "no_credentials", {"coordinates": {"lat": 1.0, "lon": 2.0}})
        client = session.connect()
        _send(client, "DLQ 5")
        [row], waiting = _read_batch(client)
        assert row["id"] == a.id and waiting == 1
        assert row["dlq"]["error"] == "no_credentials"
        assert row["dlq"]["body"] == {"coordinates": {"lat": 1.0, "lon": 2.0}}
        _send(client, protocol.format_ack([a.id]))
        assert client.readline(timeout=2.0).decode() == protocol.format_acked(1)
        _send(client, "DONE")
        assert client.readline(timeout=2.0).decode() == "OK acked=1 failed=0 dlq=0"

    def test_a_failed_replay_stays_in_the_dlq(self, buf, session):
        a = _reading("critical")
        buf.enqueue(a)
        buf.mark_dlq(a.id, "no_credentials", {"keep": 1})
        client = session.connect()
        _send(client, "DLQ 5")
        _read_batch(client)
        _send(client, protocol.format_fail(a.id, "http_503"))
        _send(client, "DONE")
        client.readline(timeout=2.0)
        [(_, dlq)] = buf.fetch_dlq(10)
        assert dlq["error"] == "http_503" and dlq["attempts"] == 2 and dlq["body"] == {"keep": 1}

    def test_disconnect_before_reporting_changes_nothing(self, buf, session):
        a = _reading("critical")
        buf.enqueue(a)
        client = session.connect()
        _send(client, "PULL 10")
        _read_batch(client)
        client.close()
        time.sleep(0.2)
        assert [x.id for x in buf.fetch_unsent()] == [a.id]

    def test_a_fail_for_an_id_not_in_this_batch_is_ignored(self, buf, session):
        a, other = _reading("critical"), _reading("medium")
        buf.enqueue(a)
        client = session.connect()
        _send(client, "PULL 10")
        _read_batch(client)
        buf.enqueue(other)
        _send(client, protocol.format_fail(other.id, "no_network"))
        _send(client, "DONE")
        assert client.readline(timeout=2.0).decode() == "OK acked=0 failed=0 dlq=0"

    def test_failures_and_the_queue_are_logged(self, buf, session, caplog):
        a = _reading("critical")
        buf.enqueue(a)
        client = session.connect()
        with caplog.at_level("INFO", logger="cv_argus.sender.server"):
            _send(client, "PULL 10")
            _read_batch(client)
            _send(client, protocol.format_fail(a.id, "no_credentials"))
            _send(client, "DONE")
            client.readline(timeout=2.0)
            time.sleep(0.1)
        assert "couldn't deliver drowsiness critical (eyes_closed)" in caplog.text
        assert "dead-letter queue 1" in caplog.text and "no_credentials 1" in caplog.text

    def test_housekeeping_dead_letters_what_the_esp32_never_picked_up(self, buf, caplog):
        buf.enqueue(_reading("critical"))
        listener = FakeListener()
        server = SenderServer(buf, listener, accept_timeout=0.05, outbox_max_age_seconds=0.0)
        with caplog.at_level("WARNING", logger="cv_argus.sender.server"):
            server.start()
            deadline = time.monotonic() + 3.0
            while buf.counts()["dlq"] == 0 and time.monotonic() < deadline:
                time.sleep(0.05)
            server.stop()
            server.join(timeout=3.0)
        assert buf.counts()["dlq"] == 1
        assert "esp32_unreachable" in caplog.text


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

    def test_a_new_incident_gets_a_new_episode(self):
        rows = _run(["none"] * 3 + ["critical"] * 3 + ["none"] * 3 + ["critical"] * 3)
        eps = {r.payload["episode"] for r in rows if r.payload["severity"] == "critical"}
        assert len(eps) == 2

    def test_a_severity_change_carries_this_frames_probabilities(self):
        rows = _run(["none"] * 4 + ["critical"])
        last = rows[-1]
        assert last.payload["severity"] == "critical"
        assert last.payload["probabilities"] == [0.05, 0.95]

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


def test_inject_resolve_uses_the_given_episode(tmp_path, monkeypatch):
    monkeypatch.setenv("BUFFER_DIR", str(tmp_path))
    inject.main(["none", "--episode", "ep1"])
    b = open_buffer()
    try:
        [row] = b.fetch_unsent()
    finally:
        b.close()
    assert row.payload["episode"] == "ep1" and row.payload["previous_severity"] == "critical"


def test_status_lists_dead_letters(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BUFFER_DIR", str(tmp_path))
    b = open_buffer()
    a = _reading("critical")
    b.enqueue(a)
    b.mark_dlq(a.id, "no_credentials", {"severity_level": "critical"})
    b.close()
    assert buffer_status.main(["--bodies"]) == 0
    out = capsys.readouterr().out
    assert "dead-letter queue:                    1" in out
    assert "no_credentials" in out and a.id in out and '"severity_level":"critical"' in out
