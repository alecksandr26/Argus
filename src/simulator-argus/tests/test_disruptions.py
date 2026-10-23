"""The optional demo disruptions' windows: none at chance 0, one inside the route at chance 1."""
from __future__ import annotations

import random

import pytest

from simulator_argus.constants import (
    DLQ_MAX_ATTEMPTS,
    LIVE_RETRY_WINDOW_S,
    SIMULATOR_REPORT_INTERVAL_S,
)
from simulator_argus.disruptions import (
    GPS_LOSS_SECONDS,
    OUTAGE_AROUND_CRITICAL_SECONDS,
    OUTAGE_SECONDS,
    in_window,
    plan_window,
    plan_window_around,
)


def test_chance_0_never_plans_a_window():
    assert all(plan_window(0.0, 1200, OUTAGE_SECONDS) is None for _ in range(100))


@pytest.mark.parametrize("seconds", [OUTAGE_SECONDS, GPS_LOSS_SECONDS])
@pytest.mark.parametrize("total_ticks", [5, 600, 1200])
def test_chance_1_plans_one_window_inside_the_route(seconds, total_ticks):
    for _ in range(50):
        start, end = plan_window(1.0, total_ticks, seconds)
        assert 1 <= start < end <= total_ticks - 1
        if total_ticks > seconds[1] + 2:
            assert seconds[0] <= end - start <= seconds[1]


def test_the_chance_is_per_route():
    random.seed(7)
    planned = sum(plan_window(0.3, 1200, OUTAGE_SECONDS) is not None for _ in range(1000))
    assert 230 <= planned <= 370


def test_outage_is_long_enough_for_a_critical_to_reach_the_dlq():
    assert OUTAGE_SECONDS[0] > 60.0  # LIVE_RETRY_CRITICAL_MS


def test_in_window_is_half_open():
    assert not in_window(None, 3)
    assert [in_window((2, 4), t) for t in range(5)] == [False, False, True, True, False]


# --- the outage placed around a critical, for a dependable replay demo -----------------------


def test_around_critical_window_fits_the_replay_budget():
    before, after = OUTAGE_AROUND_CRITICAL_SECONDS
    live = LIVE_RETRY_WINDOW_S["critical"]
    # Raised inside the outage, dead-lettered by it, and the network back before the remaining
    # DLQ attempts (one per report) have run out.
    budget = live + (DLQ_MAX_ATTEMPTS - 1) * SIMULATOR_REPORT_INTERVAL_S
    assert before > 0 and live < after < budget


def test_around_critical_window_spans_the_anchor():
    for _ in range(20):
        assert plan_window_around(1.0, 300, 1200) == (295, 370)


def test_around_critical_window_needs_the_chance_and_an_anchor():
    assert all(plan_window_around(0.0, 300, 1200) is None for _ in range(50))
    assert plan_window_around(1.0, None, 1200) is None


@pytest.mark.parametrize("anchor", [1, 3, 1150, 1198])
def test_around_critical_window_is_clipped_to_the_route(anchor):
    start, end = plan_window_around(1.0, anchor, 1200)
    assert 1 <= start < end <= 1199
    assert start <= anchor
