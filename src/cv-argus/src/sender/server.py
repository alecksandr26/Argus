"""`SenderServer` — the accept loop that answers the ESP32's PULL/ACK protocol (see
`protocol.py`), backed by a `Transport`/`Listener` (Bluetooth in production, `FakeTransport`/
`FakeListener` in tests).

**Critical invariant**: `buffer.mark_sent()` is called ONLY after an `ACK` line actually
arrives — never right after streaming rows via `PULL`'s response. If the connection drops
between `END` and `ACK`, `readline()` returns `None`, nothing gets marked sent, and those rows
are re-served verbatim on the next `PULL`. This makes an interrupted pull idempotent by
construction: a duplicate delivery is possible (the ESP32 could receive the same alert twice if
it crashes after receiving but before acking), a **lost** alert is not. Backend-side dedup by
`Alert.id` is out of scope for this repo — flagged, not solved, same as the case of the ESP32
pulling successfully but then failing its own HTTP relay to the backend.

One session at a time (sequential accept loop) — matches the one-Pi-one-ESP32 topology assumed
throughout this design; a concurrent-client upgrade would be a change to this file, not to the
protocol itself, and isn't built speculatively here.
"""

import logging
import threading

from ..alerts import serialization as alerts_serialization
from ..buffer import Buffer
from . import protocol
from .transport import Listener, Transport

logger = logging.getLogger(__name__)


class SenderServer:
    """`start()`/`stop()`/`join()`/`is_alive` mirror `pipeline.stage.Pipeline`'s naming, same
    rationale as `orchestrator.Orchestrator` — so `main.py` can treat all three top-level
    components (Pipeline, Orchestrator, SenderServer) uniformly, even though this isn't a
    `Pipeline`/`Stage` subclass.
    """

    def __init__(self, buffer: Buffer, listener: Listener, *, accept_timeout: float = 1.0) -> None:
        self._buffer = buffer
        self._listener = listener
        self._accept_timeout = accept_timeout
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="sender", daemon=False)
        self._thread.start()

    def stop(self) -> None:
        """Signal the accept loop to stop after its current session/wait. Does not join — see
        `join()`."""
        self._stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            transport = self._listener.accept(timeout=self._accept_timeout)
            if transport is None:
                continue
            try:
                self._handle_session(transport)
            except Exception:
                # One bad session shouldn't take down the accept loop -- the ESP32 just
                # reconnects and tries again next poll.
                logger.exception("sender: session handler crashed")
            finally:
                transport.close()
        self._listener.close()
        logger.info("sender: stopped")

    def _handle_session(self, transport: Transport) -> None:
        line = transport.readline(timeout=5.0)
        if line is None:
            return
        n = protocol.parse_pull(line.decode())
        if n is None:
            logger.debug("sender: malformed request %r, dropping session", line)
            return

        alerts = self._buffer.fetch_unsent(limit=n)
        for alert in alerts:
            transport.write((alerts_serialization.to_json(alert) + "\n").encode())
        transport.write((protocol.END_LINE + "\n").encode())
        if not alerts:
            return  # nothing to ack

        ack_line = transport.readline(timeout=10.0)
        if ack_line is None:
            # Disconnected before ACK -- see the module docstring: nothing gets marked sent, so
            # these rows are safely re-served on the next PULL.
            return
        ids = protocol.parse_ack(ack_line.decode())
        if ids is None:
            logger.debug("sender: malformed ACK %r, dropping session", ack_line)
            return

        updated = self._buffer.mark_sent(ids)
        transport.write((protocol.format_acked(updated) + "\n").encode())
