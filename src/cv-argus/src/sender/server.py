"""`SenderServer` — the accept loop that answers the ESP32's Bluetooth protocol v2 (see
`protocol.py`; contract C1 in `docs/designs/final-tweaks-contracts.md`), backed by a
`Transport`/`Listener` (Bluetooth in production, `FakeTransport`/`FakeListener` in tests).

**Critical invariant**: `buffer.mark_sent()` is called ONLY after an `ACK` line actually
arrives — never right after streaming rows. If the connection drops after `END`, `readline()`
returns `None`, nothing changes, and those rows are re-served verbatim on the next `PULL`. This
makes an interrupted pull idempotent by construction: a duplicate delivery is possible (the
backend deduplicates by the record id, `client_event_id`), a **lost** alert is not.

One session at a time (sequential accept loop) — matches the one-Pi-one-ESP32 topology assumed
throughout this design; a concurrent-client upgrade would be a change to this file, not to the
protocol itself, and isn't built speculatively here.

**Sessions** (the first line decides): `CONFIG` answers with the truck's `DeviceConfig` — the
backend credentials provisioned from the dashboard, plus the optional network settings and SMS
plate/phone — or `NOCONFIG`; `PULL <n>` serves the live
outbox; `DLQ <k>` serves one dead letter (newest first, round-robin); a session that opens with
`ACK`/`FAIL` reports on rows the ESP32 took in an earlier session. After a pull the ESP32 reports
per row, in any order: `ACK` (delivered, or nothing to send) marks it sent; `FAIL <reason>
<body>` applies the dead-letter rules (`Buffer.apply_fail`: critical -> DLQ or archive, anything
else -> archive; a FAIL resent for a dead letter that is no longer leased is ignored, so a resend
never counts an attempt twice); `TAKEN` leases the rows its sender task now holds for 120 s. Each
report line takes effect the moment it is read (`ACKED`/`LEASED` answer at once); `DONE` only
asks for the `OK` summary, so a session that breaks before `OK` keeps every line already applied
and the ESP32 may safely resend outcomes. Each served record carries `age_s` (seconds since the event,
computed now, see `alerts.age_seconds`), and a dead letter its `dlq` object.

`allowed_peers` (the `ESP32_BT_MAC` env var) optionally restricts every session to this truck's
own ESP32. A MAC can be spoofed, so this is a second layer on top of the encrypted, paired link
`BluetoothSppListener` requires, not a replacement for it.

**Housekeeping.** Records the ESP32 never picks up (the link is down) expire after
`outbox_max_age_seconds`: a critical goes to the dead-letter queue, a medium/driver OK to the
archive, keepalives are dropped. The queue's state is logged: every failure as it happens, and a
summary every `queue_log_seconds` while anything is waiting.

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
from dataclasses import dataclass, field

from ..alerts import Alert
from ..alerts import serialization as alerts_serialization
from ..buffer import Buffer
from ..buffer.store import FAIL_ARCHIVED, FAIL_DEAD_LETTERED, FAIL_REQUEUED, event_kind
from . import protocol
from .device_config import DeviceConfig
from .transport import Listener, Transport

logger = logging.getLogger(__name__)

_MIN_ACCEPT_BACKOFF_S = 0.1
_MAX_ACCEPT_BACKOFF_S = 5.0
# No session for this long and the ESP32 counts as gone. It polls every 1-5 s.
DEFAULT_LINK_QUIET_SECONDS = 15.0
# A live record the ESP32 hasn't picked up after this long expires (contract C1.6).
DEFAULT_OUTBOX_MAX_AGE_SECONDS = 60.0
# How often the queue summary is logged while anything is waiting.
DEFAULT_QUEUE_LOG_SECONDS = 30.0
# The report is awaited in steps this long, checking in between whether the ESP32 has already
# reconnected (it reset mid-session, BT-20) or the server is stopping.
_REPORT_POLL_S = 1.0
_HOUSEKEEPING_INTERVAL_S = 5.0


@dataclass
class _Session:
    """What one session's report lines did, for the `OK` line and the logs."""

    peer: str | None
    served: dict[str, Alert]
    replay: bool = False
    acked: int = 0
    leased: int = 0
    delivered: list[Alert] = field(default_factory=list)
    taken: list[Alert] = field(default_factory=list)
    failed: list[tuple[Alert | None, str, str]] = field(default_factory=list)  # alert, reason, outcome


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
        # The ESP32 sorts every served row into ACK/TAKEN before DONE, so this is generous.
        ack_timeout: float = 30.0,
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

    # --- sessions --------------------------------------------------------------------------

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
        request = line.decode().rstrip("\r")
        if protocol.parse_config_request(request):
            self._answer_config(transport)
            return
        k = protocol.parse_dlq(request)
        if k is not None:
            letters = self._buffer.serve_dlq(limit=k)
            self._serve(transport, [a for a, _ in letters], {a.id: d for a, d in letters})
            return
        n = protocol.parse_pull(request)
        if n is not None:
            self._serve(transport, self._buffer.fetch_unsent(limit=n), {})
            return
        if protocol.parse_ack(request) is not None or protocol.parse_fail(request) is not None:
            # A report session: results for rows taken in an earlier session.
            self._report(transport, _Session(transport.peer, {}), first_line=request)
            return
        logger.warning("sender: malformed request %r, dropping session", line[:80])

    def _serve(self, transport: Transport, alerts: list[Alert], dead_letters: dict) -> None:
        """Streams `alerts` (live, or one dead letter with its `dlq` object), each with its
        serve-time `age_s`, then `END <dlq> <outbox>`, then applies what the ESP32 reports.
        Anything not reported stays exactly where it was, so an interrupted session loses
        nothing."""
        for alert in alerts:
            data = alerts_serialization.to_dict(alert)
            data["age_s"] = alerts_serialization.age_seconds(alert)
            if alert.id in dead_letters:
                data["dlq"] = dead_letters[alert.id]
            transport.write((json.dumps(data, separators=(",", ":")) + "\n").encode())
        transport.write((protocol.format_end(*self._waiting()) + "\n").encode())
        session = _Session(transport.peer, {a.id: a for a in alerts}, replay=bool(dead_letters))
        self._report(transport, session)

    def _report(self, transport: Transport, session: _Session, first_line: str | None = None) -> None:
        """Reads report lines (ACK / FAIL / TAKEN, any order) until `DONE`, a disconnect, the
        ESP32 reconnecting, the server stopping, or `ack_timeout` in total."""
        deadline = time.monotonic() + self._ack_timeout
        pending = first_line
        while True:
            if pending is not None:
                text, pending = pending, None
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._stop_event.is_set():
                    break
                line = transport.readline(timeout=min(_REPORT_POLL_S, remaining))
                if line is None:
                    # Disconnected, or the ESP32 is already back on a new connection: either
                    # way, whatever wasn't reported is safely re-served next time.
                    if transport.eof:
                        break
                    if self._listener.pending():
                        logger.warning("sender: ESP32 reconnected mid-session (it reset?) -- "
                                       "dropping the old session, nothing it didn't report is lost")
                        break
                    continue
                text = line.decode(errors="replace").rstrip("\r")
            if text == protocol.DONE_LINE:
                result = protocol.format_result(
                    session.acked, len(session.failed), session.leased, *self._waiting()
                )
                transport.write((result + "\n").encode())
                break
            if not self._apply_report_line(transport, text, session):
                logger.debug("sender: unexpected line %r, ending session", text[:80])
                break
        self._log_delivery(session.peer, session.delivered)
        self._log_session(session)

    def _apply_report_line(self, transport: Transport, text: str, session: _Session) -> bool:
        ids = protocol.parse_ack(text)
        if ids is not None:
            known = self._lookup(ids, session)
            updated = self._buffer.mark_sent(ids)
            session.acked += updated
            session.delivered.extend(known)
            transport.write((protocol.format_acked(updated) + "\n").encode())
            return True
        fail = protocol.parse_fail(text)
        if fail is not None:
            alert_id, reason, body = fail
            [alert] = self._lookup([alert_id], session) or [None]
            outcome = self._buffer.apply_fail(alert_id, reason, body)
            if outcome is not None:
                session.failed.append((alert, reason, outcome))
            return True
        ids = protocol.parse_taken(text)
        if ids is not None:
            # Only rows served in this same session can be taken (contract C1.3).
            served = [i for i in dict.fromkeys(ids) if i in session.served]
            leased = self._buffer.lease(served)
            session.leased += leased
            session.taken.extend(session.served[i] for i in served)
            transport.write((protocol.format_leased(leased) + "\n").encode())
            return True
        return False

    def _lookup(self, ids: list[str], session: _Session) -> list[Alert]:
        """The records behind these ids, for the logs: served in this session, or (a report
        session) still waiting in the buffer."""
        found = [session.served[i] for i in dict.fromkeys(ids) if i in session.served]
        missing = [i for i in dict.fromkeys(ids) if i not in session.served]
        if missing:
            peek = getattr(self._buffer, "peek", None)
            if peek is not None:
                found.extend(peek(missing))
        return found

    def _waiting(self) -> tuple[int, int]:
        waiting = getattr(self._buffer, "waiting", None)
        return waiting() if waiting is not None else (0, 0)

    # --- logs ------------------------------------------------------------------------------

    def _log_session(self, session: _Session) -> None:
        """One line per session that delivered, took or failed a backend event; a session of
        keepalives only stays at debug level."""
        peer = session.peer or "?"
        for alert, reason, outcome in session.failed:
            what = _describe(alert) if alert is not None else "a record"
            if outcome == FAIL_REQUEUED:
                logger.info("sender: ESP32 %s: replay of %s pre-empted by a live event, back in "
                            "the dead-letter queue", peer, what)
            elif outcome == FAIL_DEAD_LETTERED:
                logger.warning("sender: ESP32 %s couldn't deliver %s -> dead-letter queue (%s)",
                               peer, what, reason)
            elif outcome == FAIL_ARCHIVED:
                logger.warning("sender: ESP32 %s gave up on %s -> archive (%s)", peer, what, reason)
        events = [a for a in session.delivered + session.taken if event_kind(a.payload)]
        if not events and not session.failed:
            logger.debug("sender: ESP32 %s acked %d record(s)", peer, session.acked)
            return
        what = "replay" if session.replay else "session"
        logger.info(
            "sender: ESP32 %s %s: acked %d, took %d, failed %d | %s",
            peer, what, session.acked, session.leased, len(session.failed), self._queue_summary(),
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
        if c.get("leased"):
            text += f", held by the ESP32 {c['leased']}"
        if c.get("archived"):
            text += f", archive {c['archived']}"
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
            moved, archived, dropped = expire(cutoff_ms)
            if moved or archived:
                logger.warning(
                    "sender: not picked up by the ESP32 in %.0f s: %d critical(s) -> dead-letter "
                    "queue, %d other alert(s) -> archive (esp32_unreachable); %d stale "
                    "keepalive(s) dropped | %s",
                    self._outbox_max_age_seconds, moved, archived, dropped, self._queue_summary(),
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


def _describe(alert: Alert) -> str:
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
