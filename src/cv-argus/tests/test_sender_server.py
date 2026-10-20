"""`sender/server.py` — the full PULL/ACK protocol over fakes plus a real `tmp_path` `Buffer`,
since the "never lose, only mark sent on ACK" guarantee is a cross-module contract worth testing
against real persistence, not just mocked calls.
"""

import json

from cv_argus.alerts import Alert, AlertKind
from cv_argus.buffer import open_buffer
from cv_argus.sender import FakeListener, FakeTransport, SenderServer, protocol


def _drowsy_alert(source_id="cam0"):
    return Alert.new(
        AlertKind.DROWSINESS,
        level=2,
        source_id=source_id,
        payload={"class_name": "Drowsy", "probabilities": [0.2, 0.8]},
    )


def _pull_and_collect(client, n):
    """Drives the client side of one PULL exchange up to (and including) reading `END`. Returns
    the list of raw JSON lines received (not including END)."""
    client.write((protocol.format_pull(n) + "\n").encode())
    lines = []
    while True:
        line = client.readline(timeout=2.0)
        assert line is not None, "server closed the connection unexpectedly"
        if line.decode() == protocol.END_LINE:
            break
        lines.append(line.decode())
    return lines


def test_full_happy_path_empties_the_buffer(tmp_path):
    buf = open_buffer(tmp_path / "b.sqlite3")
    a, b, c = _drowsy_alert("a"), _drowsy_alert("b"), _drowsy_alert("c")
    for alert in (a, b, c):
        buf.enqueue(alert)

    listener = FakeListener()
    server = SenderServer(buf, listener, accept_timeout=0.1)
    server.start()
    try:
        client, server_side = FakeTransport.pair()
        listener.offer(server_side)

        lines = _pull_and_collect(client, 10)
        assert len(lines) == 3
        ids = [json.loads(line)["id"] for line in lines]
        assert set(ids) == {a.id, b.id, c.id}

        client.write((protocol.format_ack(ids) + "\n").encode())
        acked_line = client.readline(timeout=2.0)
        assert acked_line.decode() == protocol.format_acked(3)
    finally:
        server.stop()
        server.join(timeout=2.0)

    assert buf.fetch_unsent() == []
    buf.close()


def test_disconnect_before_ack_leaves_rows_unsent(tmp_path):
    buf = open_buffer(tmp_path / "b.sqlite3")
    alert = _drowsy_alert()
    buf.enqueue(alert)

    listener = FakeListener()
    server = SenderServer(buf, listener, accept_timeout=0.1)
    server.start()
    try:
        client, server_side = FakeTransport.pair()
        listener.offer(server_side)

        _pull_and_collect(client, 10)
        client.close()  # disconnect right after END, before sending ACK
    finally:
        server.stop()
        server.join(timeout=2.0)

    assert [a.id for a in buf.fetch_unsent()] == [alert.id]
    buf.close()


def test_second_pull_reserves_the_still_unsent_alert(tmp_path):
    buf = open_buffer(tmp_path / "b.sqlite3")
    alert = _drowsy_alert()
    buf.enqueue(alert)

    listener = FakeListener()
    server = SenderServer(buf, listener, accept_timeout=0.1)
    server.start()
    try:
        # First session: dropped before ACK.
        client1, server_side1 = FakeTransport.pair()
        listener.offer(server_side1)
        _pull_and_collect(client1, 10)
        client1.close()

        # Second session: same alert must still be there, unchanged.
        client2, server_side2 = FakeTransport.pair()
        listener.offer(server_side2)
        lines = _pull_and_collect(client2, 10)
        assert len(lines) == 1
        assert json.loads(lines[0])["id"] == alert.id
        client2.write((protocol.format_ack([alert.id]) + "\n").encode())
        assert client2.readline(timeout=2.0).decode() == protocol.format_acked(1)
    finally:
        server.stop()
        server.join(timeout=2.0)

    assert buf.fetch_unsent() == []
    buf.close()


def test_malformed_pull_does_not_crash_the_accept_loop(tmp_path):
    buf = open_buffer(tmp_path / "b.sqlite3")
    listener = FakeListener()
    server = SenderServer(buf, listener, accept_timeout=0.1)
    server.start()
    try:
        bad_client, bad_server_side = FakeTransport.pair()
        listener.offer(bad_server_side)
        bad_client.write(b"GARBAGE\n")
        assert bad_client.readline(timeout=1.0) is None  # session just closes, no response

        # The accept loop must still be alive and able to serve a second, well-formed session.
        buf.enqueue(_drowsy_alert())
        good_client, good_server_side = FakeTransport.pair()
        listener.offer(good_server_side)
        lines = _pull_and_collect(good_client, 10)
        assert len(lines) == 1
    finally:
        server.stop()
        server.join(timeout=2.0)
    buf.close()


def test_empty_pull_sends_only_end_and_no_ack_round_trip(tmp_path):
    buf = open_buffer(tmp_path / "b.sqlite3")
    listener = FakeListener()
    server = SenderServer(buf, listener, accept_timeout=0.1)
    server.start()
    try:
        client, server_side = FakeTransport.pair()
        listener.offer(server_side)
        lines = _pull_and_collect(client, 10)
        assert lines == []
        client.close()
    finally:
        server.stop()
        server.join(timeout=2.0)
    buf.close()


def test_start_stop_join_lifecycle_with_no_connections(tmp_path):
    buf = open_buffer(tmp_path / "b.sqlite3")
    listener = FakeListener()
    server = SenderServer(buf, listener, accept_timeout=0.05)
    server.start()
    assert server.is_alive
    server.stop()
    server.join(timeout=2.0)
    assert not server.is_alive
    buf.close()


# --- Corner cases: partial ACKs, limits, slow or broken clients (SND-01..10) ---------------------

import socket  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402

from cv_argus.sender.bluetooth_transport import BluetoothSppTransport  # noqa: E402
from cv_argus.sender.transport import Listener  # noqa: E402


class _Running:
    """A started SenderServer over a FakeListener, plus a helper to open client sessions."""

    def __init__(self, buf, **kwargs):
        self.buf = buf
        self.listener = FakeListener()
        self.server = SenderServer(buf, self.listener, accept_timeout=0.05, **kwargs)
        self.server.start()

    def connect(self):
        client, server_side = FakeTransport.pair()
        self.listener.offer(server_side)
        return client

    def stop(self):
        self.server.stop()
        self.server.join(timeout=2.0)


@pytest.fixture
def running(tmp_path):
    buf = open_buffer(tmp_path / "b.sqlite3")
    r = _Running(buf)
    yield r
    r.stop()
    buf.close()


def _send(client, line):
    client.write((line + "\n").encode())


def test_partial_ack_leaves_the_rest_unsent(running):
    alerts = [_drowsy_alert(str(i)) for i in range(3)]
    for a in alerts:
        running.buf.enqueue(a)
    client = running.connect()
    ids = [json.loads(line)["id"] for line in _pull_and_collect(client, 10)]
    _send(client, protocol.format_ack(ids[:1]))
    assert client.readline(timeout=2.0).decode() == protocol.format_acked(1)
    assert [a.id for a in running.buf.fetch_unsent()] == ids[1:]


def test_ack_with_unknown_and_duplicate_ids_reports_actual_count(running):
    alert = _drowsy_alert()
    running.buf.enqueue(alert)
    client = running.connect()
    _pull_and_collect(client, 10)
    _send(client, f"ACK {alert.id},{alert.id},deadbeef")
    assert client.readline(timeout=2.0).decode() == protocol.format_acked(1)
    assert running.buf.unsent_count() == 0


def test_pull_n_limits_rows(running):
    for i in range(7):
        running.buf.enqueue(_drowsy_alert(str(i)))
    client = running.connect()
    assert len(_pull_and_collect(client, 4)) == 4


def test_client_that_never_sends_pull_doesnt_block_the_next_one(running):
    running.buf.enqueue(_drowsy_alert())
    running.connect()  # connects and goes silent
    second = running.connect()
    started = time.monotonic()
    _send(second, protocol.format_pull(10))
    # One session at a time: the silent client holds the server for its 5 s first-line wait.
    first_line = second.readline(timeout=8.0)
    assert first_line is not None and json.loads(first_line.decode())["kind"] == "drowsiness"
    assert time.monotonic() - started < 8.0  # served after the silent one timed out


def test_non_utf8_first_line_does_not_kill_the_accept_loop(running):
    running.buf.enqueue(_drowsy_alert())
    bad = running.connect()
    bad.write(b"\xff\xfe\xfd\n")
    good = running.connect()
    assert len(_pull_and_collect(good, 10)) == 1
    assert running.server.is_alive


def test_ack_arriving_in_a_later_session_still_marks_sent(running):
    """The ESP32 relays the batch over HTTP before ACKing; if that took longer than the Pi's
    ACK wait, it must be able to ACK on its next connection instead of re-pulling forever."""
    alert = _drowsy_alert()
    running.buf.enqueue(alert)
    first = running.connect()
    _pull_and_collect(first, 10)
    first.close()  # disconnected before ACKing

    second = running.connect()
    _send(second, protocol.format_ack([alert.id]))
    assert second.readline(timeout=2.0).decode() == protocol.format_acked(1)
    assert running.buf.unsent_count() == 0


def test_pull_with_crlf_line_ending_is_accepted(running):
    running.buf.enqueue(_drowsy_alert())
    client = running.connect()
    client.write(b"PULL 5\r\n")
    line = client.readline(timeout=2.0)
    assert line is not None and json.loads(line.decode())["kind"] == "drowsiness"


def test_huge_pull_is_clamped_to_the_protocol_maximum(running):
    for i in range(protocol.DEFAULT_PULL_LIMIT + 10):
        running.buf.enqueue(_drowsy_alert(str(i)))
    client = running.connect()
    assert len(_pull_and_collect(client, 10**9)) == protocol.MAX_PULL_LIMIT


class _FlakyListener(Listener):
    """accept() raises like a dead Bluetooth adapter would, then recovers."""

    def __init__(self, inner, failures):
        self.inner = inner
        self.failures = failures

    def accept(self, timeout=None):
        if self.failures:
            self.failures -= 1
            raise OSError("adapter reset")
        return self.inner.accept(timeout)

    def close(self):
        self.inner.close()


def test_listener_error_is_retried_not_fatal(tmp_path):
    buf = open_buffer(tmp_path / "b.sqlite3")
    buf.enqueue(_drowsy_alert())
    inner = FakeListener()
    server = SenderServer(buf, _FlakyListener(inner, failures=2), accept_timeout=0.05)
    server.start()
    try:
        client, server_side = FakeTransport.pair()
        inner.offer(server_side)
        assert len(_pull_and_collect(client, 10)) == 1
    finally:
        server.stop()
        server.join(timeout=2.0)
        buf.close()


def test_bluetooth_transport_bounds_an_endless_line():
    ours, theirs = socket.socketpair()
    transport = BluetoothSppTransport(ours)
    theirs.sendall(b"A" * 200_000)  # a client that never sends a newline
    assert transport.readline(timeout=0.5) is None
    assert len(transport._buf) <= 4096
    transport.close()
    theirs.close()


def test_bluetooth_transport_splits_lines_and_handles_disconnect():
    ours, theirs = socket.socketpair()
    transport = BluetoothSppTransport(ours)
    theirs.sendall(b"PULL 1\nACK a,b\n")
    assert transport.readline(timeout=1.0) == b"PULL 1"
    assert transport.readline(timeout=1.0) == b"ACK a,b"
    theirs.close()
    assert transport.readline(timeout=1.0) is None
    transport.close()


# --- Link visibility logging ---------------------------------------------------------------------


def _reading(severity, reason):
    return Alert.new(
        AlertKind.DROWSINESS,
        level=2,
        source_id="cam0",
        payload={"severity": severity, "reason": reason, "class_name": "Drowsy",
                 "probabilities": [0.05, 0.95]},
    )


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_logs_connected_once_then_link_down_after_quiet(tmp_path, caplog):
    caplog.set_level("INFO", logger="cv_argus.sender.server")
    buf = open_buffer(tmp_path / "b.sqlite3")
    r = _Running(buf, link_quiet_seconds=0.2)
    try:
        for _ in range(2):
            client = r.connect()
            _pull_and_collect(client, 1)
            client.close()
        _wait_for(lambda: "link down?" in caplog.text)
    finally:
        r.stop()
        buf.close()
    assert caplog.text.count("connected over Bluetooth") == 1


def test_logs_each_delivered_severity_change_once(running, caplog):
    caplog.set_level("INFO", logger="cv_argus.sender.server")
    for alert in (_reading("medium", "eyes closed 1.6 s"),
                  _reading("critical", "eyes closed 3.2 s"),
                  _reading("critical", "eyes closed 3.4 s")):
        running.buf.enqueue(alert)
    client = running.connect()
    ids = [json.loads(line)["id"] for line in _pull_and_collect(client, 50)]
    _send(client, protocol.format_ack(ids))
    assert client.readline(timeout=2.0) == protocol.format_acked(3).encode()

    _wait_for(lambda: "severity=critical" in caplog.text)
    assert caplog.text.count("severity=medium (eyes closed 1.6 s)") == 1
    assert caplog.text.count("severity=critical") == 1
    critical = [rec for rec in caplog.records if "severity=critical" in rec.getMessage()]
    assert critical[0].levelname == "WARNING"


def test_unacked_records_are_not_logged_as_delivered(running, caplog):
    caplog.set_level("INFO", logger="cv_argus.sender.server")
    running.buf.enqueue(_reading("critical", "eyes closed 3.2 s"))
    client = running.connect()
    _pull_and_collect(client, 50)
    client.close()  # no ACK
    time.sleep(0.2)
    assert "severity=critical" not in caplog.text
