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

A session can also be a `CONFIG` request (see `protocol.py`): this answers with the truck's
`DeviceConfig` — the backend credentials provisioned from the dashboard — or `NOCONFIG`.
`allowed_peers` (the `ESP32_BT_MAC` env var) optionally restricts every session to this truck's
own ESP32. A MAC can be spoofed, so this is a second layer on top of the encrypted, paired link
`BluetoothSppListener` requires, not a replacement for it.

**Link visibility.** The ESP32 opens a fresh connection on every poll, so there's no long-lived
link to watch. The log says when sessions start (`ESP32 <mac> connected`) and when they stop for
`link_quiet_seconds` (`link down?`) -- transitions only, so a steady link stays quiet. Each change
in the rule severity the ESP32 acked (e.g. into `critical`) is logged once, which is the Pi-side
proof an alert was actually delivered.
"""

import logging
import threading
import time
from collections.abc import Iterable

from ..alerts import serialization as alerts_serialization
from ..buffer import Buffer
from . import protocol
from .device_config import DeviceConfig
from .transport import Listener, Transport

logger = logging.getLogger(__name__)

_MIN_ACCEPT_BACKOFF_S = 0.1
_MAX_ACCEPT_BACKOFF_S = 5.0
# No session for this long and the ESP32 counts as gone. It polls every 1-5 s.
DEFAULT_LINK_QUIET_SECONDS = 15.0


class SenderServer:
    """`start()`/`stop()`/`join()`/`is_alive` mirror `pipeline.stage.Pipeline`'s naming, same
    rationale as `orchestrator.Orchestrator` — so `main.py` can treat all three top-level
    components (Pipeline, Orchestrator, SenderServer) uniformly, even though this isn't a
    `Pipeline`/`Stage` subclass.
    """

    def __init__(
        self,
        buffer: Buffer,
        listener: Listener,
        *,
        accept_timeout: float = 1.0,
        ack_timeout: float = 10.0,
        device_config: DeviceConfig | None = None,
        allowed_peers: Iterable[str] | None = None,
        link_quiet_seconds: float = DEFAULT_LINK_QUIET_SECONDS,
    ) -> None:
        self._buffer = buffer
        self._listener = listener
        self._device_config = device_config
        self._allowed_peers = (
            frozenset(p.strip().upper() for p in allowed_peers if p.strip())
            if allowed_peers is not None
            else None
        ) or None
        self._accept_timeout = accept_timeout
        self._ack_timeout = ack_timeout
        self._link_quiet_seconds = link_quiet_seconds
        self._link_peer: str | None = None  # None = no live link
        self._last_session_at = 0.0
        self._last_delivered_severity: str | None = None
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
        backoff = _MIN_ACCEPT_BACKOFF_S
        while not self._stop_event.is_set():
            try:
                transport = self._listener.accept(timeout=self._accept_timeout)
            except Exception:
                # A Bluetooth adapter reset / bluetoothd restart surfaces here. Retrying with a
                # backoff keeps the sender alive instead of silently ending the thread (SND-10).
                logger.exception("sender: accept failed, retrying in %.1fs", backoff)
                self._stop_event.wait(backoff)
                backoff = min(backoff * 2, _MAX_ACCEPT_BACKOFF_S)
                continue
            backoff = _MIN_ACCEPT_BACKOFF_S
            if transport is None:
                self._check_link_quiet()
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
        if self._allowed_peers is not None and (
            (transport.peer or "").upper() not in self._allowed_peers
        ):
            logger.warning("sender: rejecting session from unlisted peer %s", transport.peer)
            return
        self._note_session(transport.peer)
        line = transport.readline(timeout=5.0)
        if line is None:
            return
        request = line.decode()
        if protocol.parse_config_request(request):
            self._answer_config(transport)
            return
        ack_ids = protocol.parse_ack(request)
        if ack_ids is not None:
            # A standalone ACK for rows pulled in an earlier session (see protocol.py).
            updated = self._buffer.mark_sent(ack_ids)
            transport.write((protocol.format_acked(updated) + "\n").encode())
            return
        n = protocol.parse_pull(request)
        if n is None:
            logger.warning("sender: malformed request %r, dropping session", line[:80])
            return

        alerts = self._buffer.fetch_unsent(limit=n)
        for alert in alerts:
            transport.write((alerts_serialization.to_json(alert) + "\n").encode())
        transport.write((protocol.END_LINE + "\n").encode())
        if not alerts:
            return  # nothing to ack

        ack_line = transport.readline(timeout=self._ack_timeout)
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
        acked = set(ids)
        self._log_delivery(transport.peer, [a for a in alerts if a.id in acked])

    def _note_session(self, peer: str | None) -> None:
        peer = peer or "?"
        if self._link_peer != peer:
            logger.info("sender: ESP32 %s connected over Bluetooth", peer)
        self._link_peer = peer
        self._last_session_at = time.monotonic()

    def _check_link_quiet(self) -> None:
        if self._link_peer is None:
            return
        quiet = time.monotonic() - self._last_session_at
        if quiet >= self._link_quiet_seconds:
            logger.warning(
                "sender: no session from ESP32 %s for %.0f s -- Bluetooth link down?",
                self._link_peer,
                quiet,
            )
            self._link_peer = None

    def _log_delivery(self, peer: str | None, delivered: list) -> None:
        """Logs each change in the rule severity the ESP32 has acked -- once, not per keepalive."""
        for alert in delivered:
            severity = alert.payload.get("severity")
            if severity is None or severity == self._last_delivered_severity:
                continue
            self._last_delivered_severity = severity
            reason = alert.payload.get("reason") or alert.payload.get("class_name", "")
            log = logger.warning if severity == "critical" else logger.info
            log("sender: ESP32 %s received severity=%s (%s)", peer or "?", severity, reason)

    def _answer_config(self, transport: Transport) -> None:
        if self._device_config is None:
            logger.warning("sender: ESP32 asked for CONFIG but this Pi has no device credentials")
            transport.write((protocol.NOCONFIG_LINE + "\n").encode())
            return
        # Never log the key itself -- only which truck's credentials went out.
        logger.info("sender: sent device credentials for truck %s", self._device_config.truck_id)
        transport.write((protocol.format_config(self._device_config.to_wire()) + "\n").encode())
