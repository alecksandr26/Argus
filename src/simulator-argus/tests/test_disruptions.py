"""The optional demo disruptions' windows: none at chance 0, one inside the route at chance 1."""
from __future__ import annotations

import random

import pytest

from simulator_argus.disruptions import GPS_LOSS_SECONDS, OUTAGE_SECONDS, in_window, plan_window


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
