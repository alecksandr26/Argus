"""Unit tests for polyline movement, scenario shapes and timing, weighted scenario choice, the
OSRM fallback, and the virtual truck's combined reports (cadence, events, retries, dead
letters, route end) against a mocked backend -- no real backend, Docker, or network needed."""
from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from simulator_argus.client import BackendClient
from simulator_argus.events import EpisodeTracker, Reading
from simulator_argus.fleet import VirtualTruckSpec
from simulator_argus.osrm_client import fetch_route_geometry
from simulator_argus.scenarios import (
    DrowsyEscalationScenario,
    NoFaceScenario,
    Scenario,
    build_scenario,
    choose_scenario_name,
    parse_scenario_weights,
)
from simulator_argus.virtual_truck import VirtualTruck, interpolate_along_polyline

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




# --- scenarios: readings, turned into events by the same rule the devices use -------------


def _events(scenario, ticks: int):
    """Runs a scenario through EpisodeTracker; returns [(tick, event)]."""
    tracker = EpisodeTracker()
    fired = []
    for tick in range(ticks):
        event = tracker.update(scenario.reading(tick), now=float(tick))
        if event is not None:
            fired.append((tick, event))
    return fired


def test_normal_scenario_fires_nothing_when_its_blip_chance_is_0():
    scenario = Scenario(total_ticks=500, blip_chance=0.0)
    assert scenario.blip_window is None
    assert {scenario.reading(t) for t in range(500)} == {Reading("low", "ok")}
    assert _events(Scenario(total_ticks=500, blip_chance=0.0), 500) == []


def test_normal_scenario_blip_is_one_medium_alert_then_one_ok():
    scenario = Scenario(total_ticks=1000, blip_chance=1.0, tick_seconds=1.0)
    start, end = scenario.blip_window
    fired = _events(scenario, 1000)

    assert [(e.kind, e.severity, e.alert_type) for _, e in fired] == [
        ("alert", "medium", "head_down"),
        ("ok", None, None),
    ]
    assert [tick for tick, _ in fired] == [start, end]
    assert fired[0][1].episode == fired[1][1].episode


@pytest.mark.parametrize("total_ticks", [10, 60, 600, 1200, 14400])
def test_normal_scenario_blip_holds_30_s_to_2_min_and_ends_inside_the_route(total_ticks):
    for _ in range(50):
        scenario = Scenario(total_ticks=total_ticks, blip_chance=1.0, tick_seconds=1.0)
        start, end = scenario.blip_window
        assert 1 <= start < end <= total_ticks - 1  # the alert and its OK both happen en route
        if total_ticks >= 122:  # room for the full dwell
            assert 30 <= end - start <= 120
        assert [scenario.reading(t).severity for t in range(start, end)] == ["medium"] * (
            end - start
        )


@pytest.mark.parametrize("total_ticks", [600, 1200])  # the 10 min AWS session, the 20 min default
def test_normal_routes_get_at_most_one_blip_and_about_half_stay_fine(total_ticks):
    random.seed(1234)
    blips_per_route = []
    for _ in range(400):
        scenario = Scenario(total_ticks=total_ticks)  # the default blip chance (0.5)
        alerts = [e for _, e in _events(scenario, total_ticks) if e.kind == "alert"]
        assert all(e.severity == "medium" for e in alerts)
        blips_per_route.append(len(alerts))

    assert max(blips_per_route) <= 1
    quiet_share = blips_per_route.count(0) / len(blips_per_route)
    assert 0.40 <= quiet_share <= 0.60


def test_normal_blip_start_is_spread_over_the_whole_route():
    random.seed(99)
    starts = [Scenario(total_ticks=1200, blip_chance=1.0).blip_window[0] for _ in range(400)]
    early = sum(1 for s in starts if s < 600)
    assert 120 <= early <= 280  # uniform: about half in each half of the route
    assert min(starts) < 120 and max(starts) > 1000


def test_drowsy_escalation_is_one_medium_one_critical_upgrade_and_one_ok_per_cycle():
    scenario = DrowsyEscalationScenario(total_ticks=1200)
    fired = _events(scenario, scenario._cycle_length)

    assert [(e.kind, e.severity, e.alert_type) for _, e in fired] == [
        ("alert", "medium", "eyes_closed"),
        ("alert", "critical", "eyes_closed"),
        ("ok", None, None),
    ]
    assert [tick for tick, _ in fired] == [
        scenario._escalate_at,
        scenario._peak_at,
        scenario._recover_at,
    ]
    assert len({e.episode for _, e in fired}) == 1
    critical = fired[1][1]
    assert critical.ai_metadata["scores"] == {"not_drowsy": 0.12, "drowsy": 0.88}


def test_drowsy_escalation_repeats_with_a_new_episode_each_cycle():
    scenario = DrowsyEscalationScenario(total_ticks=1200)
    fired = _events(scenario, scenario._cycle_length * 2)
    kinds = [(e.kind, e.severity) for _, e in fired]
    assert kinds == [("alert", "medium"), ("alert", "critical"), ("ok", None)] * 2
    assert fired[0][1].episode != fired[3][1].episode


@pytest.mark.parametrize("total_ticks", [10, 40, 240, 2000, 14400])
def test_drowsy_escalation_timing_scales_with_total_ticks(total_ticks):
    scenario = DrowsyEscalationScenario(total_ticks=total_ticks)
    assert scenario._escalate_at < scenario._peak_at < scenario._recover_at < scenario._cycle_length


def test_no_face_scenario_is_one_critical_without_a_medium_step_then_one_ok():
    scenario = NoFaceScenario(total_ticks=1200)
    fired = _events(scenario, 1200)

    assert [(e.kind, e.severity, e.alert_type) for _, e in fired] == [
        ("alert", "critical", "no_face"),
        ("ok", None, None),
    ]
    assert fired[0][0] == scenario._away_from
    assert fired[1][0] == scenario._away_until
    assert 1 <= scenario._away_from <= 400  # in the first third of the route
    assert 30 <= scenario._away_until - scenario._away_from <= 90
    assert fired[0][1].ai_metadata == {"model": "simulator", "scores": None}


@pytest.mark.parametrize("cls", [NoFaceScenario, DrowsyEscalationScenario])
def test_only_normal_takes_a_blip(cls):
    # no_face and drowsy_escalation tell their own story; normal's blip never rides on top.
    for _ in range(20):
        scenario = cls(total_ticks=1200, blip_chance=1.0)
        assert scenario.blip_window is None
        assert "head_down" not in {e.alert_type for _, e in _events(scenario, 1200)}


def test_no_face_scenario_has_one_incident_per_route():
    for _ in range(50):
        scenario = NoFaceScenario(total_ticks=1200, blip_chance=1.0)
        kinds = [(e.kind, e.severity) for _, e in _events(scenario, 1200)]
        assert kinds == [("alert", "critical"), ("ok", None)]


def test_build_scenario_returns_matching_profile():
    assert build_scenario("normal", total_ticks=10).name == "normal"
    assert build_scenario("drowsy_escalation", total_ticks=10).name == "drowsy_escalation"
    assert build_scenario("no_face", total_ticks=10).name == "no_face"


def test_panic_scenario_is_gone():
    with pytest.raises(KeyError):
        build_scenario("panic", total_ticks=10)


# --- weighted, randomized scenario choice ---------------------------------------------------


def test_parse_scenario_weights_valid_spec():
    weights = parse_scenario_weights("normal=0.5,drowsy_escalation=0.3,no_face=0.2")
    assert weights == {"normal": 0.5, "drowsy_escalation": 0.3, "no_face": 0.2}


def test_parse_scenario_weights_maps_the_retired_panic_to_no_face():
    weights = parse_scenario_weights("normal=0.45,drowsy_escalation=0.35,panic=0.20")
    assert weights == {"normal": 0.45, "drowsy_escalation": 0.35, "no_face": 0.20}


def test_parse_scenario_weights_drops_unknown_names_and_malformed_entries():
    weights = parse_scenario_weights("normal=0.5,not_a_real_scenario=0.9,broken,no_face=abc")
    assert weights == {"normal": 0.5}


def test_parse_scenario_weights_falls_back_to_equal_split_on_garbage():
    weights = parse_scenario_weights("nonsense")
    assert set(weights.keys()) == {"normal", "drowsy_escalation", "no_face"}
    assert all(w > 0 for w in weights.values())


def test_choose_scenario_name_only_returns_weighted_names():
    weights = {"normal": 1.0}
    assert all(choose_scenario_name(weights) == "normal" for _ in range(20))


def test_choose_scenario_name_respects_zero_weight_exclusion():
    weights = {"normal": 1.0, "no_face": 0.0}
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


# --- the virtual truck's combined reports, against a mocked backend ------------------------


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class _Backend:
    """A scripted `/api/devices/me/report`: answers each request with the next behaviour in
    `script` ("ok", "down" = 503, "refused" = connection error, "finished" = route ended), or
    keeps answering "ok" when the script runs out. Records every request body."""

    def __init__(self, script=()) -> None:
        self.script = list(script)
        self.bodies: list[dict] = []
        self.headers: list[httpx.Headers] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/devices/me/report"
        body = json.loads(request.content)
        self.bodies.append(body)
        self.headers.append(request.headers)
        behaviour = self.script.pop(0) if self.script else "ok"
        if behaviour == "refused":
            raise httpx.ConnectError("connection refused", request=request)
        if behaviour == "down":
            return httpx.Response(503, json={"detail": "unavailable"})
        if behaviour == "no_route":
            return httpx.Response(
                200,
                json={
                    "points": {"stored": 0, "route": None, "status": "no_active_route"},
                    "event": None if "event" not in body else
                    {"status": "rejected", "reason": "no_route", "id_alert": None},
                    "active_route": None,
                },
            )
        if behaviour in ("old_event", "finished_no_route"):
            # The event is rejected route_finished; "old_event": the truck's current route is
            # active (a stale event), "finished_no_route": the truck has no active route left.
            active = "r1" if behaviour == "old_event" else None
            return httpx.Response(
                200,
                json={
                    "points": {
                        "stored": len(body["points"]) if active else 0,
                        "route": active,
                        "status": "ok" if active else "no_active_route",
                    },
                    "event": {"status": "rejected", "reason": "route_finished", "id_alert": None},
                    "active_route": active,
                },
            )
        finished = behaviour == "finished"
        event = None
        if body.get("event"):
            event = (
                {"status": "rejected", "reason": "route_finished", "id_alert": None}
                if finished
                else {"status": "created", "reason": None, "id_alert": "a1"}
            )
        return httpx.Response(
            200,
            json={
                "points": {
                    "stored": 0 if finished else len(body["points"]),
                    "route": None if finished else "r1",
                    "status": "route_finished" if finished else "ok",
                },
                "event": event,
                "active_route": None if finished else "r1",
            },
        )


class _Script:
    """A scenario stand-in: `readings[tick]`, `low` after the end."""

    name = "scripted"

    def __init__(self, readings: dict[int, Reading]) -> None:
        self.readings = readings

    def reading(self, tick: int) -> Reading:
        return self.readings.get(tick, Reading("low", "ok"))


_LOW = Reading("low", "ok")
_MEDIUM = Reading("medium", "eyes_closed", 0.6)
_CRITICAL = Reading("critical", "eyes_closed", 0.9)


def _truck(backend: _Backend, readings: dict[int, Reading] | None = None):
    clock = _Clock()
    departure = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    spec = VirtualTruckSpec(
        route_id="r1",
        truck_id="t1",
        device_key="device-key",
        geometry=[(25.6866, -100.3161), (25.4260, -100.9959)],
        departure=departure,
        arrival=departure + timedelta(minutes=20),
        scenario="normal",
    )
    client = BackendClient("http://backend", transport=httpx.MockTransport(backend.handler))
    truck = VirtualTruck(
        client,
        spec,
        clock=clock,
        wall_clock=lambda: departure + timedelta(seconds=clock.t - 1000.0),
    )
    truck._scenario = _Script(readings or {})
    return truck, clock, client


async def _run(truck, clock, ticks: range) -> None:
    for tick in ticks:
        clock.t = 1000.0 + tick
        await truck.step(tick)


async def test_points_only_report_goes_every_5_s_with_one_point_per_second():
    backend = _Backend()
    truck, clock, client = _truck(backend)
    await _run(truck, clock, range(0, 11))
    await client.aclose()

    assert len(backend.bodies) == 2  # at t=5 and t=10
    first, second = backend.bodies
    assert "event" not in first
    assert [p["age_s"] for p in first["points"]] == [5.0, 4.0, 3.0, 2.0, 1.0, 0.0]
    assert len(second["points"]) == 5
    assert all(p["vigilance"] == "low" and p["last_known"] is False for p in first["points"])
    assert backend.headers[0]["x-device-api-key"] == "device-key"
    assert "authorization" not in backend.headers[0]


async def test_an_event_goes_at_once_with_the_points_so_far():
    backend = _Backend()
    truck, clock, client = _truck(backend, {2: _MEDIUM, 3: _MEDIUM})
    await _run(truck, clock, range(0, 3))
    await client.aclose()

    assert len(backend.bodies) == 1
    body = backend.bodies[0]
    assert len(body["points"]) == 3
    assert body["points"][-1]["vigilance"] == "medium"
    event = body["event"]
    assert (event["kind"], event["severity"], event["alert_type"]) == (
        "alert",
        "medium",
        "eyes_closed",
    )
    assert event["delivery"] == "live" and event["queued_seconds"] == 0
    assert event["age_s"] == 0.0
    assert event["position"]["last_known"] is False
    assert not truck.queue.live  # delivered


async def test_one_alert_per_incident_plus_upgrade_and_driver_ok():
    backend = _Backend()
    readings = {t: _MEDIUM for t in range(2, 6)} | {t: _CRITICAL for t in range(6, 9)}
    truck, clock, client = _truck(backend, readings)
    await _run(truck, clock, range(0, 12))
    await client.aclose()

    events = [b["event"] for b in backend.bodies if "event" in b]
    assert [(e["kind"], e["severity"]) for e in events] == [
        ("alert", "medium"),
        ("alert", "critical"),
        ("ok", None),
    ]
    assert len({e["episode"] for e in events}) == 1


async def test_a_failed_live_event_is_retried_once_per_second():
    backend = _Backend(["down", "refused", "ok"])
    truck, clock, client = _truck(backend, {1: _MEDIUM, 2: _MEDIUM, 3: _MEDIUM, 4: _MEDIUM})
    await _run(truck, clock, range(0, 5))
    await client.aclose()

    events = [b.get("event") for b in backend.bodies]
    assert len(events) == 3 and all(e is not None for e in events)
    assert len({e["client_event_id"] for e in events}) == 1
    assert [e["age_s"] for e in events] == [0.0, 1.0, 2.0]
    # The points of a failed report are dropped, never resent.
    assert [len(b["points"]) for b in backend.bodies] == [2, 1, 1]
    assert not truck.queue.live


async def test_a_critical_dead_letters_after_60_s_and_replays_as_dlq_when_back():
    backend = _Backend(["down"] * 60)
    readings = {t: _CRITICAL for t in range(1, 200)}
    truck, clock, client = _truck(backend, readings)
    await _run(truck, clock, range(0, 62))

    assert len(truck.queue.dlq) == 1
    dead = truck.queue.dlq[0]
    assert dead.attempts == 1 and dead.last_error == "http_503"
    live_attempts = [b for b in backend.bodies if b.get("event")]
    assert {b["event"]["delivery"] for b in live_attempts} == {"live"}

    # The backend is back: the replay rides on the next regular report, as delivery "dlq".
    await _run(truck, clock, range(62, 70))
    await client.aclose()
    replay = [b["event"] for b in backend.bodies if b.get("event", {}) and b["event"]["delivery"] == "dlq"]
    assert len(replay) == 1
    assert replay[0]["client_event_id"] == dead.event.client_event_id
    assert replay[0]["queued_seconds"] > 0
    assert replay[0]["age_s"] > 60
    assert not truck.queue.dlq


async def test_a_new_live_event_goes_before_a_dlq_replay():
    backend = _Backend(["down"] * 60)
    readings = {t: _CRITICAL for t in range(1, 62)} | {62: _LOW, 63: _MEDIUM}
    truck, clock, client = _truck(backend, readings)
    await _run(truck, clock, range(0, 62))
    assert len(truck.queue.dlq) == 1

    await _run(truck, clock, range(62, 64))
    await client.aclose()
    after = [b.get("event") for b in backend.bodies[-2:]]
    # t=62: the driver OK; t=63: the new medium. The dead letter waits for a quiet report.
    assert [(e["kind"], e["delivery"]) for e in after] == [("ok", "live"), ("alert", "live")]
    assert len(truck.queue.dlq) == 1


async def test_a_stopped_route_ends_the_truck():
    backend = _Backend(["ok", "finished"])
    truck, clock, client = _truck(backend)
    await _run(truck, clock, range(0, 11))
    assert truck.finished
    await client.aclose()


async def test_run_returns_once_the_route_has_ended(monkeypatch):
    # "finished" answers points.status route_finished, active_route null, and rejects the event
    # route_finished: what the backend says once the route was stopped or completed.
    backend = _Backend(["finished"])
    truck, clock, client = _truck(backend, {0: _MEDIUM})
    real_sleep = asyncio.sleep

    async def fast_sleep(_seconds):
        clock.t += 1.0
        await real_sleep(0)  # still yield, so the sender task gets to run

    monkeypatch.setattr("simulator_argus.virtual_truck.asyncio.sleep", fast_sleep)
    await asyncio.wait_for(truck.run(), timeout=5)  # would loop forever if it didn't stop
    await client.aclose()
    assert truck.finished
    assert backend.bodies[0]["event"]["severity"] == "medium"
    assert backend.bodies[0]["points"]  # the GPS read of tick 0 went with it
    assert truck.queue.finished[0].reason == "route_finished"


async def test_an_event_rejected_route_finished_while_a_route_is_active_doesnt_end_the_truck():
    # E.g. a dead-letter replay of an event from an earlier, ended route: that one event is
    # archived, but the truck's current route is live (points ok, active_route set).
    backend = _Backend(["old_event"])
    truck, clock, client = _truck(backend, {1: _CRITICAL})
    await _run(truck, clock, range(0, 2))
    await client.aclose()

    assert not truck.finished
    assert truck.active_route == "r1"
    finished = truck.queue.finished[0]
    assert (finished.outcome, finished.reason) == ("archived", "route_finished")


async def test_an_event_rejected_route_finished_with_no_active_route_ends_the_truck():
    backend = _Backend(["finished_no_route"])
    truck, clock, client = _truck(backend, {1: _MEDIUM})
    await _run(truck, clock, range(0, 2))
    await client.aclose()

    assert truck.finished
    assert truck.queue.finished[0].outcome == "dropped"


async def test_a_medium_dropped_by_its_retry_window_is_logged(caplog):
    backend = _Backend(["down"] * 100)
    truck, clock, client = _truck(backend, {t: _MEDIUM for t in range(1, 40)})
    with caplog.at_level(logging.WARNING, logger="simulator_argus.virtual_truck"):
        await _run(truck, clock, range(0, 32))
    await client.aclose()

    # Failed at t=1..30, then t=31 is past the 30 s window: expire() drops it, not a failure.
    lines = [r.getMessage() for r in caplog.records if "result=dropped" in r.getMessage()]
    assert lines == [
        "[live] truck=t1 event=medium/eyes_closed attempt=30 transport=- status=- "
        "duration=0.00s points=0 result=dropped:retry_window"
    ]
    assert not truck.queue.live and not truck.queue.dlq


async def test_a_critical_moved_to_the_dlq_by_its_live_window_is_logged(caplog):
    backend = _Backend(["down"] * 100)
    truck, clock, client = _truck(backend, {t: _CRITICAL for t in range(1, 200)})
    with caplog.at_level(logging.WARNING, logger="simulator_argus.virtual_truck"):
        await _run(truck, clock, range(0, 62))
    await client.aclose()

    lines = [r.getMessage() for r in caplog.records if "dead_lettered" in r.getMessage()]
    assert lines == [
        "[live] truck=t1 event=critical/eyes_closed attempt=60 transport=- status=- "
        "duration=0.00s points=0 result=dead_lettered:http_503"
    ]
    assert len(truck.queue.dlq) == 1


async def test_a_dead_letter_archived_after_5_attempts_is_logged(caplog):
    backend = _Backend(["down"] * 1000)
    truck, clock, client = _truck(backend, {t: _CRITICAL for t in range(1, 62)})
    with caplog.at_level(logging.WARNING, logger="simulator_argus.virtual_truck"):
        # Dead-lettered at t=61 (attempt 1). The driver OK of t=62 then holds the DLQ back for
        # its own 30 s window; after that, one replay per regular report: 4 more failures.
        await _run(truck, clock, range(0, 140))
    await client.aclose()

    lines = [r.getMessage() for r in caplog.records if "archived" in r.getMessage()]
    assert lines == [
        "[dlq] truck=t1 event=critical/eyes_closed attempt=5 transport=- status=- "
        "duration=0.00s points=0 result=archived:max_attempts"
    ]
    assert not truck.queue.dlq


async def test_gps_keeps_reading_every_tick_while_a_report_hangs():
    # run() keeps the GPS tick and the sender in separate tasks, like the ESP32's GPS loop and
    # its sender task: a slow attempt doesn't stop the points.
    gate = asyncio.Event()
    bodies: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            await gate.wait()  # the first report hangs until the test lets it go
            return httpx.Response(
                200,
                json={
                    "points": {"stored": 1, "route": "r1", "status": "ok"},
                    "event": {"status": "created", "reason": None, "id_alert": "a1"},
                    "active_route": "r1",
                },
            )
        return httpx.Response(
            200,
            json={
                "points": {"stored": 0, "route": None, "status": "route_finished"},
                "event": None,
                "active_route": None,
            },
        )

    now = datetime.now(timezone.utc)
    spec = VirtualTruckSpec(
        route_id="r1",
        truck_id="t1",
        device_key="device-key",
        geometry=[(25.6866, -100.3161), (25.4260, -100.9959)],
        departure=now,
        arrival=now + timedelta(minutes=20),
        scenario="normal",
    )
    client = BackendClient("http://backend", transport=httpx.MockTransport(handler))
    truck = VirtualTruck(client, spec, report_interval_seconds=0.05, tick_seconds=0.01)
    truck._scenario = _Script({0: _MEDIUM})

    task = asyncio.create_task(truck.run())
    await asyncio.sleep(0.2)
    assert len(bodies) == 1  # the medium's report is still in flight...
    assert len(truck._points) >= 5  # ...and the GPS kept reading meanwhile

    gate.set()
    await asyncio.wait_for(task, timeout=5)
    await client.aclose()
    assert truck.finished
    assert len(bodies) == 2
    assert len(bodies[1]["points"]) >= 5  # the points read during the hang went next


async def test_no_active_route_slows_points_only_reports_to_30_s():
    backend = _Backend(["no_route"] * 6)
    truck, clock, client = _truck(backend)
    await _run(truck, clock, range(0, 41))
    await client.aclose()

    # t=5 learns there's no active route; the next points-only report waits 30 s (t=35).
    assert len(backend.bodies) == 2
    assert truck.active_route is None
    assert not truck.finished


# --- optional demo disruptions: a network outage and a GPS loss -----------------------------


async def test_a_simulated_outage_dead_letters_a_critical_and_replays_it_afterwards(caplog):
    backend = _Backend()
    readings = {t: _CRITICAL for t in range(10, 13)}
    truck, clock, client = _truck(backend, readings)
    truck._outage = (5, 85)
    with caplog.at_level(logging.INFO, logger="simulator_argus.virtual_truck"):
        await _run(truck, clock, range(0, 95))
    await client.aclose()

    # Nothing reached the backend during the outage; the attempts failed with no_network.
    assert "transport=none" in caplog.text and "failed:no_network" in caplog.text
    assert "result=dead_lettered:no_network" in caplog.text
    assert "result=dropped:retry_window" in caplog.text  # the driver OK of t=13

    events = [b["event"] for b in backend.bodies if b.get("event")]
    assert [(e["kind"], e["severity"], e["delivery"]) for e in events] == [
        ("alert", "critical", "dlq")
    ]
    replay = events[0]
    assert replay["queued_seconds"] > 0 and replay["age_s"] >= 75
    assert replay["position"]["last_known"] is False
    assert not truck.queue.dlq and not truck.queue.live


async def test_a_simulated_gps_loss_sends_the_last_known_position():
    backend = _Backend()
    truck, clock, client = _truck(backend, {t: _MEDIUM for t in range(5, 20)})
    truck._gps_loss = (3, 8)
    await _run(truck, clock, range(0, 11))
    await client.aclose()

    first, second = backend.bodies
    points = first["points"] + second["points"]
    assert [p["last_known"] for p in points] == [False] * 3 + [True] * 5 + [False] * 3
    fix = points[2]
    lost = points[3:8]
    assert all((p["lat"], p["lon"], p["speed"]) == (fix["lat"], fix["lon"], 0.0) for p in lost)
    assert [p["location_age_s"] for p in lost] == [1.0, 2.0, 3.0, 4.0, 5.0]

    # The medium of t=5 went with that last-known position and no speed.
    event = first["event"]
    assert event["position"] == {
        "lat": fix["lat"],
        "lon": fix["lon"],
        "last_known": True,
        "location_age_s": 3.0,
    }
    assert event["speed"] is None


async def test_with_no_fix_yet_an_event_goes_with_position_unknown_and_no_points():
    backend = _Backend()
    truck, clock, client = _truck(backend, {1: _MEDIUM, 2: _MEDIUM})
    truck._gps_loss = (0, 3)
    await _run(truck, clock, range(0, 2))
    await client.aclose()

    [body] = backend.bodies
    assert body["points"] == []
    assert body["event"]["position"] is None and body["event"]["speed"] is None


async def test_disruptions_are_off_by_default():
    truck, _, client = _truck(_Backend())
    await client.aclose()
    assert truck._outage is None and truck._gps_loss is None
