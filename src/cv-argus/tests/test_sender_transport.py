"""`sender/transport.py` — `FakeTransport`/`FakeListener` semantics (the hermetic stand-ins for
Bluetooth used by every other sender/ test)."""

from cv_argus.sender import FakeListener, FakeTransport


def test_pair_write_on_one_end_is_readable_on_the_other():
    a, b = FakeTransport.pair()
    a.write(b"hello\n")
    assert b.readline(timeout=1.0) == b"hello"


def test_pair_is_bidirectional():
    a, b = FakeTransport.pair()
    a.write(b"ping\n")
    b.write(b"pong\n")
    assert b.readline(timeout=1.0) == b"ping"
    assert a.readline(timeout=1.0) == b"pong"


def test_readline_reassembles_multiple_chunks():
    a, b = FakeTransport.pair()
    a._send_q.put(b"hel")
    a._send_q.put(b"lo\n")
    assert b.readline(timeout=1.0) == b"hello"


def test_readline_leaves_a_second_line_buffered():
    a, b = FakeTransport.pair()
    a.write(b"first\nsecond\n")
    assert b.readline(timeout=1.0) == b"first"
    assert b.readline(timeout=1.0) == b"second"


def test_close_makes_the_other_ends_readline_return_none():
    a, b = FakeTransport.pair()
    a.close()
    assert b.readline(timeout=1.0) is None


def test_readline_with_nothing_queued_times_out_and_returns_none():
    _a, b = FakeTransport.pair()
    assert b.readline(timeout=0.05) is None


def test_fake_listener_accept_returns_none_when_nothing_offered():
    listener = FakeListener()
    assert listener.accept(timeout=0.05) is None


def test_fake_listener_accept_returns_offered_transport():
    listener = FakeListener()
    transport, _peer = FakeTransport.pair()
    listener.offer(transport)
    assert listener.accept(timeout=1.0) is transport
