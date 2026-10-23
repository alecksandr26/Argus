"""The ESP32 sending rules the simulator mirrors: priority, live retry windows, the critical-only
dead-letter queue (newest first, round-robin, 5 attempts) and rejections."""
from __future__ import annotations

import itertools

from simulator_argus.delivery import DeliveryQueue
from simulator_argus.events import DeviceEvent

_ids = itertools.count()


def _event(kind: str = "alert", severity: str | None = "critical", t: float = 0.0) -> DeviceEvent:
    return DeviceEvent(
        client_event_id=f"evt{next(_ids)}",
        episode="abcdefabcdef",
        kind=kind,
        severity=severity if kind == "alert" else None,
        alert_type="eyes_closed" if kind == "alert" else None,
        occurred_at=t,
        position=None,
        speed=None,
        ai_metadata=None,
    )


def _ok(t: float = 0.0) -> DeviceEvent:
    return _event("ok", None, t)


def _dead_letter(queue: DeliveryQueue, t: float) -> object:
    """Puts one critical through a failed live window, so it lands in the DLQ with 1 attempt."""
    item = queue.add_live(_event(t=t), t)
    queue.mark_failed(item, "no_network", t + 60.0)
    assert item.in_dlq
    return item


# --- priority -----------------------------------------------------------------------------


def test_live_priority_is_critical_then_medium_then_ok():
    queue = DeliveryQueue()
    ok = queue.add_live(_ok(), 0.0)
    medium = queue.add_live(_event(severity="medium"), 0.0)
    critical = queue.add_live(_event(severity="critical"), 0.0)

    assert queue.next_live(0.0) is critical
    queue.mark_delivered(critical)
    assert queue.next_live(0.0) is medium
    queue.mark_delivered(medium)
    assert queue.next_live(0.0) is ok


def test_oldest_first_within_a_priority():
    queue = DeliveryQueue()
    first = queue.add_live(_event(severity="medium"), 0.0)
    queue.add_live(_event(severity="medium"), 1.0)
    assert queue.next_live(1.0) is first


def test_dlq_is_offered_only_when_no_live_event_is_pending():
    queue = DeliveryQueue()
    dead = _dead_letter(queue, 0.0)
    live = queue.add_live(_ok(100.0), 100.0)

    assert queue.next_dlq() is None
    queue.mark_delivered(live)
    assert queue.next_dlq() is dead


def test_a_live_event_waiting_out_its_send_gap_still_blocks_the_dlq():
    queue = DeliveryQueue()
    _dead_letter(queue, 0.0)
    live = queue.add_live(_event(severity="medium", t=100.0), 100.0)
    queue.mark_failed(live, "timeout", 100.0)

    assert queue.next_live(100.5) is None  # inside MIN_SEND_GAP
    assert queue.next_dlq() is None
    assert queue.next_live(101.0) is live


# --- live retry windows -------------------------------------------------------------------


def test_a_failed_live_event_is_retried_after_the_min_send_gap():
    queue = DeliveryQueue()
    item = queue.add_live(_event(severity="medium"), 0.0)
    assert queue.mark_failed(item, "http_503", 0.2) is None
    assert queue.next_live(1.0) is None
    assert queue.next_live(1.2) is item


def test_medium_and_ok_are_dropped_after_30_s():
    queue = DeliveryQueue()
    medium = queue.add_live(_event(severity="medium"), 0.0)
    ok = queue.add_live(_ok(), 0.0)

    assert queue.mark_failed(medium, "timeout", 29.0) is None
    finished = queue.mark_failed(medium, "timeout", 30.0)
    assert (finished.outcome, finished.reason) == ("dropped", "retry_window")

    queue.expire(30.0)  # never attempted: the window still runs out
    assert not queue.live
    assert [f.item for f in queue.finished] == [medium, ok]
    assert not queue.dlq


def test_a_critical_retries_for_60_s_then_dead_letters_as_attempt_1():
    queue = DeliveryQueue()
    item = queue.add_live(_event(), 0.0)
    for t in range(0, 59):
        assert queue.mark_failed(item, "no_network", float(t)) is None
        assert not item.in_dlq
    assert queue.mark_failed(item, "no_network", 60.0) is None
    assert item.in_dlq
    assert item.attempts == 1
    assert item.dlq_since == 60.0
    assert item.last_error == "no_network"
    assert queue.dlq == [item] and not queue.live


def test_a_critical_that_never_got_an_attempt_enters_the_dlq_with_0_attempts():
    queue = DeliveryQueue()
    item = queue.add_live(_event(), 0.0)
    assert queue.next_live(61.0) is None
    assert item.in_dlq and item.attempts == 0


# --- DLQ ----------------------------------------------------------------------------------


def test_dlq_archives_after_5_failed_attempts_in_total():
    queue = DeliveryQueue()
    item = _dead_letter(queue, 0.0)  # attempt 1
    for n in range(2, 5):
        assert queue.next_dlq() is item
        assert queue.mark_failed(item, "no_network", 100.0 + n) is None
        assert item.attempts == n
    assert queue.next_dlq() is item
    finished = queue.mark_failed(item, "no_network", 200.0)
    assert (finished.outcome, finished.reason) == ("archived", "max_attempts")
    assert item.attempts == 5
    assert not queue.dlq


def test_dlq_serves_newest_first_round_robin():
    queue = DeliveryQueue()
    old = _dead_letter(queue, 0.0)
    new = _dead_letter(queue, 10.0)

    served = []
    for _ in range(4):
        item = queue.next_dlq()
        served.append(item)
        queue.mark_failed(item, "no_network", 500.0)
    assert served == [new, old, new, old]


def test_a_row_entering_the_dlq_joins_the_current_round():
    queue = DeliveryQueue()
    old = _dead_letter(queue, 0.0)
    assert queue.next_dlq() is old  # old is now in round 1
    queue.mark_failed(old, "no_network", 100.0)
    newer = _dead_letter(queue, 200.0)
    # It enters at round 1 (the current minimum), not 0, so it can't take several attempts in a
    # row ahead of `old`; ties go to the newest.
    assert newer.dlq_round == 1
    assert queue.next_dlq() is newer
    assert queue.next_dlq() is old


def test_dlq_wire_is_delivery_dlq_with_queued_seconds():
    queue = DeliveryQueue()
    item = _dead_letter(queue, 0.0)  # dead-lettered at t=60
    wire = item.to_wire(95.0)
    assert wire["delivery"] == "dlq"
    assert wire["queued_seconds"] == 35.0
    assert wire["age_s"] == 95.0


# --- outcomes -----------------------------------------------------------------------------


def test_delivered_leaves_the_queue():
    queue = DeliveryQueue()
    item = queue.add_live(_event(), 0.0)
    queue.mark_delivered(item)
    assert not queue.live and queue.delivered == [item]


def test_a_rejected_critical_is_archived_and_a_rejected_medium_dropped():
    queue = DeliveryQueue()
    critical = _dead_letter(queue, 0.0)
    medium = queue.add_live(_event(severity="medium"), 0.0)

    assert queue.mark_rejected(critical, "route_finished").outcome == "archived"
    assert queue.mark_rejected(medium, "route_finished").outcome == "dropped"
    assert not queue.live and not queue.dlq
