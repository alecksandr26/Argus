"""`Transport`/`Listener` — the Bluetooth-agnostic duplex abstraction `sender/` is built on, plus
`FakeTransport`/`FakeListener`, the in-memory pair used for hermetic tests. The real Bluetooth
implementation is `bluetooth_transport.py` — see that module's docstring for why it's kept
separate and only lazily imported.
"""

import queue
from abc import ABC, abstractmethod


class Transport(ABC):
    """One accepted connection's raw byte-stream duplex. A concrete `Transport` represents ONE
    already-accepted client session; the accept loop itself is a separate concern — see
    `Listener` below.
    """

    @abstractmethod
    def readline(self, timeout: float | None = None) -> bytes | None:
        """Read one newline-delimited message, without the trailing newline. Returns `None` on
        timeout or a clean disconnect — callers can't tell the two apart from this alone, which
        is fine: `server.py`'s protocol handler treats both the same way (a not-yet-acked record
        stays unsent and is safely re-served on the next poll either way)."""

    @abstractmethod
    def write(self, data: bytes) -> None:
        """Write raw bytes. The caller includes the trailing newline."""

    @abstractmethod
    def close(self) -> None: ...


class Listener(ABC):
    """Accepts `Transport` sessions — the counterpart to `Transport` for the listening side."""

    @abstractmethod
    def accept(self, timeout: float | None = None) -> Transport | None:
        """Blocks up to `timeout` seconds for an incoming connection. Returns `None` on timeout
        so the accept-loop thread can re-check its stop event periodically — same pattern as
        `pipeline.stage.DEFAULT_QUEUE_GET_TIMEOUT`."""

    @abstractmethod
    def close(self) -> None: ...


class FakeTransport(Transport):
    """In-memory duplex pair for hermetic tests: two queues, one per direction. Build a
    connected pair with `FakeTransport.pair()` — one end stands in for the ESP32 client in a
    test, the other is handed to `SenderServer`.
    """

    _CLOSED = object()

    @staticmethod
    def pair() -> tuple["FakeTransport", "FakeTransport"]:
        a_to_b: queue.Queue = queue.Queue()
        b_to_a: queue.Queue = queue.Queue()
        return FakeTransport(recv_q=b_to_a, send_q=a_to_b), FakeTransport(recv_q=a_to_b, send_q=b_to_a)

    def __init__(self, recv_q: queue.Queue, send_q: queue.Queue) -> None:
        self._recv_q = recv_q
        self._send_q = send_q
        self._buf = b""
        self._closed = False

    def readline(self, timeout: float | None = None) -> bytes | None:
        while b"\n" not in self._buf:
            try:
                chunk = self._recv_q.get(timeout=timeout)
            except queue.Empty:
                return None
            if chunk is self._CLOSED:
                return None
            self._buf += chunk
        line, _, rest = self._buf.partition(b"\n")
        self._buf = rest
        return line

    def write(self, data: bytes) -> None:
        self._send_q.put(data)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._send_q.put(self._CLOSED)


class FakeListener(Listener):
    """Hands back pre-offered `FakeTransport` connections — a test calls `offer()` to queue up a
    connection for the accept loop to pick up next."""

    def __init__(self) -> None:
        self._pending: queue.Queue = queue.Queue()

    def offer(self, transport: Transport) -> None:
        self._pending.put(transport)

    def accept(self, timeout: float | None = None) -> Transport | None:
        try:
            return self._pending.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        pass
