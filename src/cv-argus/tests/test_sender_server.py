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
