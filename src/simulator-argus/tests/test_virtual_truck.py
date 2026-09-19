"""Unit tests for polyline movement, scenario timing/shape, weighted scenario choice, and the
OSRM fallback -- no real backend, Docker, or network needed."""
from __future__ import annotations

import httpx
import pytest

from simulator_argus.osrm_client import fetch_route_geometry
from simulator_argus.scenarios import (
    DrowsyEscalationScenario,
    PanicScenario,
    Scenario,
    build_scenario,
    choose_scenario_name,
    parse_scenario_weights,
)
from simulator_argus.virtual_truck import interpolate_along_polyline


# --- movement: straight-line (2-point, the no-OSRM fallback shape) ------------------------


def test_interpolate_straight_line_at_start_is_origin_within_jitter():
    origin = (19.4326, -99.1332)
    destination = (20.6597, -103.3496)
    lat, lon = interpolate_along_polyline([origin, destination], 0.0)
    assert abs(lat - origin[0]) < 0.02
    assert abs(lon - origin[1]) < 0.02


def test_interpolate_straight_line_at_end_is_destination_within_jitter():
    origin = (19.4326, -99.1332)
    destination = (20.6597, -103.3496)
    lat, lon = interpolate_along_polyline([origin, destination], 1.0)
    assert abs(lat - destination[0]) < 0.02
    assert abs(lon - destination[1]) < 0.02


def test_interpolate_clamps_fraction_outside_zero_one():
    origin = (0.0, 0.0)
    destination = (10.0, 10.0)
    lat, lon = interpolate_along_polyline([origin, destination], 5.0)
    assert abs(lat - 10.0) < 0.02
    assert abs(lon - 10.0) < 0.02


# --- movement: a real multi-point polyline (the OSRM shape) --------------------------------


def test_interpolate_multipoint_polyline_walks_through_intermediate_points():
    polyline = [(0.0, 0.0), (0.0, 1.0), (0.0, 2.0)]  # two equal-length segments
    # Halfway along total length should land near the midpoint vertex (0.0, 1.0).
    lat, lon = interpolate_along_polyline(polyline, 0.5)
    assert abs(lat - 0.0) < 0.02
    assert abs(lon - 1.0) < 0.02


def test_interpolate_multipoint_polyline_start_and_end():
    polyline = [(0.0, 0.0), (0.0, 1.0), (0.0, 2.0)]
    start_lat, start_lon = interpolate_along_polyline(polyline, 0.0)
    end_lat, end_lon = interpolate_along_polyline(polyline, 1.0)
    assert abs(start_lon - 0.0) < 0.02
    assert abs(end_lon - 2.0) < 0.02


# --- scenarios: timing scales with the route's own configured duration ---------------------


def test_normal_scenario_never_fires_an_alert():
    scenario = Scenario(total_ticks=50)
    assert all(scenario.alert(tick) is None for tick in range(50))


def test_normal_scenario_medium_blip_holds_for_minimum_dwell_ticks():
    # probability=1.0 forces a blip to start at tick 0; the minimum dwell (30s / 5s per tick)
    # guarantees at least 6 consecutive "medium" ticks, regardless of the random dwell draw.
    scenario = Scenario(total_ticks=500, medium_blip_probability=1.0, interval_seconds=5.0)
    vigilances = [scenario.vigilance(t) for t in range(6)]
    assert vigilances == ["medium"] * 6


def test_panic_scenario_medium_blip_holds_for_minimum_dwell_ticks():
    scenario = PanicScenario(total_ticks=500, medium_blip_probability=1.0, interval_seconds=5.0)
    vigilances = [scenario.vigilance(t) for t in range(6)]
    assert vigilances == ["medium"] * 6


def test_drowsy_escalation_fires_one_fusion_alert_at_peak_and_resolves():
    scenario = DrowsyEscalationScenario(total_ticks=40)
    fired = None
    resolved_tick = None
    for tick in range(scenario._cycle_length):
        event = scenario.alert(tick)
        if event is not None:
            fired = (tick, event)
        if scenario.should_resolve(tick):
            resolved_tick = tick

    assert fired is not None
    tick, event = fired
    assert tick == scenario._peak_at
    assert event.source == "fusion"
    assert event.ai_metadata is not None
    assert event.grip_status == "bad"
    assert scenario.vigilance(scenario._peak_at) == "critical"
    assert resolved_tick == scenario._recover_at


@pytest.mark.parametrize("total_ticks", [10, 40, 240, 2000])
def test_drowsy_escalation_timing_scales_with_total_ticks(total_ticks):
    scenario = DrowsyEscalationScenario(total_ticks=total_ticks)
    assert scenario._escalate_at < scenario._peak_at < scenario._recover_at < scenario._cycle_length


def test_panic_scenario_fires_exactly_one_unresolved_panic_button_alert():
    scenario = PanicScenario(total_ticks=20)
    events = [scenario.alert(tick) for tick in range(20)]
    fired = [e for e in events if e is not None]

    assert len(fired) == 1
    assert fired[0].source == "panic_button"
    assert fired[0].ai_metadata is None
    assert fired[0].grip_status is None
    assert all(not scenario.should_resolve(tick) for tick in range(20))


def test_build_scenario_returns_matching_profile():
    assert build_scenario("normal", total_ticks=10).name == "normal"
    assert build_scenario("drowsy_escalation", total_ticks=10).name == "drowsy_escalation"
    assert build_scenario("panic", total_ticks=10).name == "panic"


# --- weighted, randomized scenario choice ---------------------------------------------------


def test_parse_scenario_weights_valid_spec():
    weights = parse_scenario_weights("normal=0.5,drowsy_escalation=0.3,panic=0.2")
    assert weights == {"normal": 0.5, "drowsy_escalation": 0.3, "panic": 0.2}


def test_parse_scenario_weights_drops_unknown_names_and_malformed_entries():
    weights = parse_scenario_weights("normal=0.5,not_a_real_scenario=0.9,broken,panic=abc")
    assert weights == {"normal": 0.5}


def test_parse_scenario_weights_falls_back_to_equal_split_on_garbage():
    weights = parse_scenario_weights("nonsense")
    assert set(weights.keys()) == {"normal", "drowsy_escalation", "panic"}
    assert all(w > 0 for w in weights.values())


def test_choose_scenario_name_only_returns_weighted_names():
    weights = {"normal": 1.0}
    assert all(choose_scenario_name(weights) == "normal" for _ in range(20))


def test_choose_scenario_name_respects_zero_weight_exclusion():
    weights = {"normal": 1.0, "panic": 0.0}
    assert all(choose_scenario_name(weights) == "normal" for _ in range(50))


# --- OSRM fallback: never blocks the simulator, even when OSRM is unreachable --------------


async def test_fetch_route_geometry_falls_back_to_straight_line_on_failure():
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    origin = (19.4326, -99.1332)
    destination = (20.6597, -103.3496)

    geometry = await fetch_route_geometry(
        "http://osrm:5000", origin, destination, transport=transport
    )

    assert geometry == [origin, destination]


async def test_fetch_route_geometry_parses_a_successful_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "routes": [
                    {
                        "geometry": {
                            "coordinates": [
                                [-99.1332, 19.4326],
                                [-101.0, 20.0],
                                [-103.3496, 20.6597],
                            ]
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    origin = (19.4326, -99.1332)
    destination = (20.6597, -103.3496)

    geometry = await fetch_route_geometry(
        "http://osrm:5000", origin, destination, transport=transport
    )

    assert geometry == [(19.4326, -99.1332), (20.0, -101.0), (20.6597, -103.3496)]
