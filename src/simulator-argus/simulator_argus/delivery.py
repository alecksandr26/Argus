"""The ESP32's sending rules, as plain logic the virtual truck drives (contracts C1.6/C1.8/C2.3).

- **Priority:** live critical > live medium > live OK > dead-letter critical. A dead-letter
  replay is only offered while no live event is pending at all.
- **Live retry windows:** a pending live event is retried at most once per `MIN_SEND_GAP_S`.
  When its window (60 s critical, 30 s medium/OK) has run out, a critical moves to the
  dead-letter queue (DLQ) and a medium/OK is dropped (`retry_window`).
- **DLQ:** criticals only. Served newest first, round-robin (smallest `round`, then newest), so
  one row can't use up its attempts back to back. Every failed attempt counts, the live failure
  that dead-letters a row being attempt 1; at `DLQ_MAX_ATTEMPTS` the row is archived (kept,
  never resent).
- **Rejections** (`route_finished`, `no_route`, `too_old`, `wrong_truck`, `invalid`) end an event
  at once: archived for a critical, dropped otherwise.

The one ESP32 rule the simulator does not mirror is pre-emption between AT steps: a simulated
attempt is a single HTTP request with no steps to pre-empt between, so a live event that comes
up while an attempt is in flight (the GPS tick runs in its own task) waits for that attempt to
end, at most the 8 s attempt timeout; a dead-letter replay already in flight runs to its end and
counts. The simulator also doesn't need the Pi's `TAKEN` lease: the queue lives in one process.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Optional

from .constants import (
    DLQ_MAX_ATTEMPTS,
    LIVE_RETRY_WINDOW_S,
    MIN_SEND_GAP_S,
)
from .events import DeviceEvent

_PRIORITY = {"critical": 3, "medium": 2, "ok": 1}


@dataclass
class QueuedEvent:
    event: DeviceEvent
    seq: int  # arrival order on the device; larger = newer
    queued_at: float  # when the device got the event (start of its live window)
    next_try_at: float
    live_failures: int = 0
    attempts: int = 0  # DLQ attempt count (C1.6), the dead-lettering live failure included
    dlq_since: Optional[float] = None
    dlq_round: int = 0
    last_error: Optional[str] = None

    @property
    def in_dlq(self) -> bool:
        return self.dlq_since is not None

    def to_wire(self, now: float) -> dict:
        if self.in_dlq:
            return self.event.to_wire(now, delivery="dlq", queued_seconds=now - self.dlq_since)
        return self.event.to_wire(now)


@dataclass(frozen=True)
class Finished:
    """An event the device gave up on, in part or for good:

    - `dropped`: a medium/OK whose live window ran out (`retry_window`) or that was rejected;
    - `dead_lettered`: a critical whose live window ran out, moved to the DLQ (`reason` = its
      last failure, or `retry_window` if it never got an attempt);
    - `archived`: a critical that was rejected, or failed `DLQ_MAX_ATTEMPTS` times.

    Only `dropped`/`archived` leave the queue (they also go to `DeliveryQueue.finished`). Every
    one of them, whatever path produced it (`expire`, `next_live`, `mark_failed`,
    `mark_rejected`), is also queued as a notice for the truck to log (`drain_notices`), so
    nothing is given up silently."""

    item: QueuedEvent
    outcome: str  # "archived" | "dropped" | "dead_lettered"
    reason: str


@dataclass
class DeliveryQueue:
    min_send_gap_s: float = MIN_SEND_GAP_S
    windows_s: dict[str, float] = field(default_factory=lambda: dict(LIVE_RETRY_WINDOW_S))
    max_attempts: int = DLQ_MAX_ATTEMPTS

    def __post_init__(self) -> None:
        self.live: list[QueuedEvent] = []
        self.dlq: list[QueuedEvent] = []
        self.delivered: list[QueuedEvent] = []
        self.finished: list[Finished] = []
        self._notices: list[Finished] = []
        self._seq = itertools.count()

    # --- intake --------------------------------------------------------------------------

    def add_live(self, event: DeviceEvent, now: float) -> QueuedEvent:
        item = QueuedEvent(event=event, seq=next(self._seq), queued_at=now, next_try_at=now)
        self.live.append(item)
        return item

    @property
    def has_live_pending(self) -> bool:
        return bool(self.live)

    # --- choosing what to send ------------------------------------------------------------

    def expire(self, now: float) -> list[Finished]:
        """Gives up on every live event whose window has run out (C1.8 / plan section 1): a
        medium/OK is dropped (`retry_window`), a critical moves to the DLQ. Returns what it gave
        up on (each one is also a notice, see `drain_notices`)."""
        expired: list[Finished] = []
        for item in list(self.live):
            window = self.windows_s[item.event.priority_key]
            if now - item.queued_at >= window:
                expired.append(self._give_up_live(item, now))
        return expired

    def drain_notices(self) -> list[Finished]:
        """Everything given up on since the last call, oldest first, for the truck to log."""
        notices, self._notices = self._notices, []
        return notices

    def next_live(self, now: float) -> Optional[QueuedEvent]:
        """The live event to send now: highest priority first, oldest first within a priority,
        skipping events still inside their `min_send_gap_s` after a failed attempt. Expires
        first, so what that gives up on shows up in `drain_notices`."""
        self.expire(now)
        ready = [item for item in self.live if item.next_try_at <= now]
        if not ready:
            return None
        return max(ready, key=lambda i: (_PRIORITY[i.event.priority_key], -i.seq))

    def next_dlq(self) -> Optional[QueuedEvent]:
        """One dead-letter row, only while no live event is pending: smallest round, newest
        first. Serving it moves it to the next round whatever its outcome."""
        if self.live or not self.dlq:
            return None
        item = min(self.dlq, key=lambda i: (i.dlq_round, -i.seq))
        item.dlq_round += 1
        return item

    # --- outcomes ------------------------------------------------------------------------

    def mark_delivered(self, item: QueuedEvent) -> None:
        self._remove(item)
        self.delivered.append(item)

    def mark_rejected(self, item: QueuedEvent, reason: str) -> Finished:
        """The backend refused the event for good (C2.3): archive a critical, drop the rest."""
        self._remove(item)
        outcome = "archived" if item.event.priority_key == "critical" else "dropped"
        return self._finish(Finished(item, outcome, reason))

    def mark_failed(self, item: QueuedEvent, reason: str, now: float) -> Optional[Finished]:
        """One failed attempt (transport error, timeout, 401, 429, 5xx). Returns a `Finished`
        when this attempt made the device give up (dropped, dead-lettered or archived), else
        `None` (the event will be retried)."""
        item.last_error = reason
        if item.in_dlq:
            item.attempts += 1
            if item.attempts >= self.max_attempts:
                self._remove(item)
                return self._finish(Finished(item, "archived", "max_attempts"))
            return None

        item.live_failures += 1
        item.next_try_at = now + self.min_send_gap_s
        window = self.windows_s[item.event.priority_key]
        if now - item.queued_at >= window:
            return self._give_up_live(item, now)
        return None

    # --- internals -----------------------------------------------------------------------

    def _give_up_live(self, item: QueuedEvent, now: float) -> Finished:
        self.live.remove(item)
        if item.event.priority_key != "critical":
            return self._finish(Finished(item, "dropped", "retry_window"))
        # The live failure that dead-letters a critical is attempt 1; a critical that never got
        # an attempt (it couldn't be sent at all) enters with 0, like the Pi's
        # `esp32_unreachable` rows.
        item.attempts = 1 if item.live_failures else 0
        item.dlq_since = now
        item.dlq_round = min((other.dlq_round for other in self.dlq), default=0)
        if item.attempts >= self.max_attempts:  # only if max_attempts <= 1
            return self._finish(Finished(item, "archived", "max_attempts"))
        self.dlq.append(item)
        notice = Finished(item, "dead_lettered", item.last_error or "retry_window")
        self._notices.append(notice)
        return notice

    def _finish(self, finished: Finished) -> Finished:
        """Records an event that left the queue for good, and its notice."""
        self.finished.append(finished)
        self._notices.append(finished)
        return finished

    def _remove(self, item: QueuedEvent) -> None:
        if item in self.live:
            self.live.remove(item)
        elif item in self.dlq:
            self.dlq.remove(item)
