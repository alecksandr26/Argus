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

**Live first, dead-letter on failure** (see `protocol.py` and `buffer/store.py`). The ESP32 says
per record whether it reached the backend: `ACK` (delivered, or nothing to send) marks it sent;
`FAIL <reason> <body>` moves it to the dead-letter queue with the message the ESP32 composed. The
live outbox always goes first (`PULL`); the ESP32 asks for dead letters (`DLQ <k>`) only once it
can reach the backend again. Records the ESP32 never picks up (the link is down) are moved to the
dead-letter queue after `outbox_max_age_seconds`. The queue's state is logged: every failure as it
happens, and a summary every `queue_log_seconds` while anything is waiting.

**Link visibility.** The ESP32 opens a fresh connection on every poll, so there's no long-lived
link to watch. The log says when sessions start (`ESP32 <mac> connected`) and when they stop for
`link_quiet_seconds` (`link down?`) -- transitions only, so a steady link stays quiet. Each change
in the rule severity the ESP32 acked (e.g. into `critical`) is logged once, which is the Pi-side
proof an alert was actually delivered.
"""

import json
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
# A live record the ESP32 hasn't picked up after this long goes to the dead-letter queue.
DEFAULT_OUTBOX_MAX_AGE_SECONDS = 60.0
# How often the queue summary is logged while anything is waiting.
DEFAULT_QUEUE_LOG_SECONDS = 30.0
# After ACKED, how long to wait for the ESP32's DONE. An older client just disconnects.
_DONE_TIMEOUT_S = 1.0
_HOUSEKEEPING_INTERVAL_S = 5.0


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
        outbox_max_age_seconds: float = DEFAULT_OUTBOX_MAX_AGE_SECONDS,
        queue_log_seconds: float = DEFAULT_QUEUE_LOG_SECONDS,
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
        self._outbox_max_age_seconds = outbox_max_age_seconds
        self._queue_log_seconds = queue_log_seconds
        self._last_housekeeping_at = 0.0
        self._last_queue_log_at = time.monotonic()
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
                self._housekeeping()
                continue
            try:
                self._handle_session(transport)
            except Exception:
                # One bad session shouldn't take down the accept loop -- the ESP32 just
                # reconnects and tries again next poll.
                logger.exception("sender: session handler crashed")
            finally:
                transport.close()
            self._housekeeping()
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
        k = protocol.parse_dlq(request)
        if k is not None:
            letters = self._buffer.fetch_dlq(limit=k)
            self._serve(transport, [a for a, _ in letters], {a.id: d for a, d in letters})
            return
        n = protocol.parse_pull(request)
        if n is None:
            logger.warning("sender: malformed request %r, dropping session", line[:80])
            return
        self._serve(transport, self._buffer.fetch_unsent(limit=n), {})

    def _serve(self, transport: Transport, alerts: list, dead_letters: dict) -> None:
        """Streams `alerts` (live, or dead letters with their `dlq` info), then applies what the
        ESP32 reports back: `FAIL` lines, an `ACK`, then `DONE`. Anything not reported stays
        exactly where it was, so an interrupted session loses nothing."""
        for alert in alerts:
            data = alerts_serialization.to_dict(alert)
            if alert.id in dead_letters:
                data["dlq"] = dead_letters[alert.id]
            transport.write((json.dumps(data, separators=(",", ":")) + "\n").encode())
        transport.write((protocol.format_end(self._dlq_waiting()) + "\n").encode())
        if not alerts:
            return  # nothing to report back

        by_id = {alert.id: alert for alert in alerts}
        delivered, failed = [], []
        timeout = self._ack_timeout
        while True:
            line = transport.readline(timeout=timeout)
            if line is None:
                # Disconnected (or an older client that's done after ACKED) -- see the module
                # docstring: whatever wasn't reported is safely re-served next time.
                break
            text = line.decode(errors="replace").rstrip("\r")
            fail = protocol.parse_fail(text)
            if fail is not None:
                alert_id, reason, body = fail
                if alert_id in by_id and self._buffer.mark_dlq(alert_id, reason, body):
                    failed.append((by_id[alert_id], reason))
                continue
            ids = protocol.parse_ack(text)
            if ids is not None:
                updated = self._buffer.mark_sent(ids)
                transport.write((protocol.format_acked(updated) + "\n").encode())
                delivered.extend(by_id[i] for i in ids if i in by_id)
                timeout = _DONE_TIMEOUT_S
                continue
            if text == protocol.DONE_LINE:
                result = protocol.format_result(len(delivered), len(failed), self._dlq_waiting())
                transport.write((result + "\n").encode())
                break
            logger.debug("sender: unexpected line %r, ending session", text[:80])
            break

        self._log_delivery(transport.peer, delivered)
        self._log_session(transport.peer, delivered, failed, replay=bool(dead_letters))

    def _dlq_waiting(self) -> int:
        counts = getattr(self._buffer, "counts", None)
        return counts()["dlq"] if counts is not None else 0

    def _log_session(self, peer: str | None, delivered: list, failed: list, *, replay: bool) -> None:
        """One line per session that delivered or failed something the backend cares about (a
        transition or a heartbeat); a session of keepalives only stays at debug level."""
        bound = [a for a in delivered if _backend_bound(a)]
        for alert, reason in failed:
            logger.warning(
                "sender: ESP32 %s couldn't deliver %s -> dead-letter queue (%s)",
                peer or "?", _describe(alert), reason,
            )
        if not bound and not failed:
            logger.debug("sender: ESP32 %s took %d keepalive(s)", peer or "?", len(delivered))
            return
        what = "replayed from the dead-letter queue" if replay else "delivered"
        logger.info(
            "sender: ESP32 %s %s %d, failed %d | %s",
            peer or "?", what, len(delivered), len(failed), self._queue_summary(),
        )

    def _queue_summary(self) -> str:
        counts = getattr(self._buffer, "counts", None)
        if counts is None:
            return "queue n/a"
        c = counts()
        text = f"outbox {c['outbox']}, dead-letter queue {c['dlq']}"
        if c["dlq"]:
            oldest = c["oldest_dlq_ms"]
            age = f"oldest {_age(oldest)} ago, " if oldest else ""
            reasons = ", ".join(f"{r or '?'} {n}" for r, n in sorted(c["dlq_reasons"].items()))
            text += f" ({age}{reasons})"
        return text

    def _housekeeping(self) -> None:
        """Expires the outbox while the ESP32 is away, and logs the queue now and then. Runs on
        the accept loop's own thread, between sessions; a storage error is logged, never allowed
        to end the loop."""
        now = time.monotonic()
        if self._stop_event.is_set() or now - self._last_housekeeping_at < _HOUSEKEEPING_INTERVAL_S:
            return
        self._last_housekeeping_at = now
        try:
            self._expire_and_report(now)
        except Exception:
            logger.exception("sender: queue housekeeping failed")

    def _expire_and_report(self, now: float) -> None:
        expire = getattr(self._buffer, "expire_outbox", None)
        if expire is not None:
            cutoff_ms = int((time.time() - self._outbox_max_age_seconds) * 1000)
            moved, dropped = expire(cutoff_ms)
            if moved:
                logger.warning(
                    "sender: %d record(s) not picked up by the ESP32 in %.0f s -> dead-letter "
                    "queue (esp32_unreachable); %d stale keepalive(s) dropped | %s",
                    moved, self._outbox_max_age_seconds, dropped, self._queue_summary(),
                )
        if now - self._last_queue_log_at >= self._queue_log_seconds:
            self._last_queue_log_at = now
            counts = getattr(self._buffer, "counts", None)
            if counts is not None and (c := counts())["dlq"] + c["outbox"] > 0:
                logger.info("sender: queue: %s", self._queue_summary())

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


def _backend_bound(alert) -> bool:
    """A heartbeat, or a reading that changed the class or the severity (see buffer/store.py)."""
    return alert.kind.value == "route_status" or bool(alert.payload.get("transition"))


def _describe(alert) -> str:
    if alert.kind.value == "route_status":
        return "heartbeat"
    payload = alert.payload
    severity = payload.get("severity") or "?"
    reason = payload.get("reason") or payload.get("class_name") or ""
    return f"drowsiness {severity} ({reason})"


def _age(epoch_ms: int) -> str:
    seconds = max(0, int(time.time() - epoch_ms / 1000))
    if seconds < 120:
        return f"{seconds} s"
    if seconds < 7200:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h"
