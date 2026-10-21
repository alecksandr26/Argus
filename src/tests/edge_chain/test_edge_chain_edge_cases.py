"""Whole-chain edge cases from the chain audit: real cv-argus Orchestrator -> real Buffer/
SenderServer -> simulated ESP32 -> real backend-argus, same harness as
test_edge_chain_scenarios.py. Scenario IDs are in docs/designs/edge-cloud-scenarios.md.

Known bugs are xfail(strict=True, raises=AssertionError): a test may only count as a known
failure by failing its own assertion, never by crashing in the harness, so each one stays a real
check. The plain tests are guards that pass today and must keep passing after the fixes.
"""
from __future__ import annotations

import time as real_time
from datetime import datetime, timezone

import pytest

from app.models.status_route import StatusRoute
from cv_argus.alerts import route_status

from .test_edge_chain_scenarios import (  # noqa: F401  (pi_clock/scenario are fixtures)
    DC,
    DM,
    FPS,
    ND,
    NOFACE,
    POLL_EVERY_S,
    WALL_BASE,
    alerts_on,
    pi_clock,
    scenario,
)

pytestmark = pytest.mark.asyncio

known_bug = pytest.mark.xfail  # alias so each marker below reads as one line


async def _confirm_medium_incident(scenario):
    """The camera's severity at medium until a medium alert is posted."""
    await scenario.frames(ND, 4)
    await scenario.frames(DM, 6)
    assert [a["severity_level"] for _, a in scenario.dev.alert_posts] == ["medium"]


# --- 1. What the camera sends ------------------------------------------------------------


async def test_not_drowsy_after_an_incident_resolves_it(scenario, backend, truck_route):
    """Control for REC-18a: the same incident, followed by real Not Drowsy frames, does resolve
    in this harness today, so the No Face test below can only fail for the No Face reason."""
    route_id, _ = truck_route
    await _confirm_medium_incident(scenario)
    await scenario.frames(ND, 30)
    await scenario.drain()
    [alert] = await alerts_on(backend, route_id)
    assert alert["resolved_at"] is not None


@known_bug(strict=True, raises=AssertionError,
           reason="REC-18a/CV-04/B1: a face out of frame decays to severity none, which resolves")
async def test_no_face_mid_incident_does_not_recover(scenario, backend, truck_route):
    """The driver slumps out of frame mid-incident. With no face the rules' severity falls back
    to none, the ESP32 reads that as recovery and resolves the incident. It must stay open."""
    route_id, _ = truck_route
    await _confirm_medium_incident(scenario)
    await scenario.frames(NOFACE, 30)
    await scenario.drain()
    [alert] = await alerts_on(backend, route_id)
    assert alert["resolved_at"] is None, "a driver out of frame resolved the incident"


async def test_unsynced_pi_clock_never_becomes_the_event_time(scenario, backend, truck_route):
    """REC-20 guard. A Pi with no battery-backed clock that boots offline stamps rows with its
    last shutdown time and says so (`clock_synced: false`). Whatever the ESP32 sends as
    occurred_at, it must not be that `created_at_ms`: here it is 3 days old, so it would also
    land the record in the past. The ESP32 uses its GPS time instead."""
    route_id, _ = truck_route
    row = route_status("cam-0")
    row.created_at_ms = int((WALL_BASE - 3 * 24 * 3600) * 1000)
    row.clock_synced = False
    scenario.buffer.enqueue(row)
    await scenario.drain()
    [status] = await StatusRoute.find(StatusRoute.id_route == route_id).to_list()
    stamped = status.timestamp.replace(tzinfo=timezone.utc)
    assert abs((datetime.now(timezone.utc) - stamped).total_seconds()) < 120


async def test_two_cameras_do_not_reset_each_others_alerts(scenario, backend, truck_route):
    """cam-0 sees a drowsy driver while cam-1 (e.g. a second angle) sees Not Drowsy. The Pi keeps
    each camera's severity (and episode) apart, so cam-0's critical incident is still raised."""
    route_id, _ = truck_route
    for _ in range(int(20 * FPS)):
        scenario.orch._on_detection("cam-0", DC)
        scenario.orch._on_detection("cam-1", ND)
        scenario.orch._tick("cam-0")
        scenario.clock.now += 1.0 / FPS
        if scenario.clock.now >= scenario._next_poll:
            scenario._next_poll += POLL_EVERY_S
            await scenario.dev.poll()
    await scenario.drain()
    severities = [a["severity_level"] for a in await alerts_on(backend, route_id)]
    assert "critical" in severities, f"alerts raised: {severities}"


# --- 4. Data correctness after a backlog -------------------------------------------------


async def test_backlog_replay_does_not_stamp_relay_time_position(scenario, backend, truck_route):
    """The network is down for a stretch while the truck moves. Each heartbeat fails with the
    message composed then (where the truck was), the Pi keeps that in its dead-letter queue, and
    the replay sends exactly that, not the position at replay time (FUS-15a)."""
    route_id, _ = truck_route
    scenario.dev.wifi = False
    points = [(19.40 + i * 0.05, -99.10 - i * 0.05) for i in range(5)]
    for lat, lon in points:
        scenario.dev.gps = (lat, lon, 80.0)
        scenario.buffer.enqueue(route_status("cam-0"))
        scenario.clock.now += 60.0
        await scenario.dev.poll()  # the Pi is reachable, the backend isn't
    scenario.dev.wifi = True
    await scenario.drain()
    stored = await StatusRoute.find(StatusRoute.id_route == route_id).to_list()
    positions = {tuple(round(c, 4) for c in s.current_coordinates.coordinates) for s in stored}
    assert len(stored) == 5
    assert len(positions) == 5, f"{len(stored)} rows share {len(positions)} position(s)"


async def test_backlog_heartbeat_keeps_its_own_vigilance(scenario, backend, truck_route):
    """A heartbeat queued while the driver was fine fails to post (the status endpoint answers
    5xx for a while). Meanwhile a critical incident is raised. When the heartbeat finally goes
    through it must still say `low`, the vigilance composed when it happened (FUS-15b)."""
    route_id, _ = truck_route
    scenario.buffer.enqueue(route_status("cam-0"))  # queued while calm
    scenario.dev.status_failures = 10**6
    await scenario.frames(DC, 12)
    assert [a["severity_level"] for _, a in scenario.dev.alert_posts] == ["critical"]
    scenario.dev.status_failures = 0
    await scenario.drain()
    [status] = await StatusRoute.find(StatusRoute.id_route == route_id).to_list()
    assert status.vigilance.value == "low", f"calm heartbeat stored as {status.vigilance.value}"


# --- 5. Route lifecycle ----------------------------------------------------------------


async def test_route_change_mid_incident_has_no_stuck_records(scenario, backend, truck_route):
    """An incident is open when the operator completes the route and starts the truck's next
    one (NET-12). A write the backend refuses for good (409/422) is ACKed, not retried forever,
    the device looks its new route up after the 409, and the old incident is still resolved, so
    the queue keeps draining."""
    route_id, _ = truck_route
    await _confirm_medium_incident(scenario)
    old = (await backend.get(f"/api/routes/{route_id}")).json()
    await backend.put(f"/api/routes/{route_id}", json={"operative_status": "completed"})
    new = await backend.post(
        "/api/routes",
        json={
            "id_driver": old["id_driver"],
            "id_truck": old["id_truck"],
            "origin_name": "GDL",
            "destination_name": "MTY",
            "destination_coordinates": {"lat": 25.67, "lon": -100.31},
            "estimated_departure": "2030-01-02T08:00:00Z",
            "operative_status": "in_progress",
        },
    )
    assert new.status_code == 201
    await scenario.frames(DC, 22)  # still drowsy: escalates to critical
    await scenario.frames(ND, 12)  # then recovers
    await scenario.drain(max_polls=3)  # AssertionError if records are stuck
    old_alerts = await alerts_on(backend, route_id)
    assert all(a["resolved_at"] is not None for a in old_alerts)


# --- 6. Connectivity -------------------------------------------------------------------


@known_bug(strict=True, raises=AssertionError,
           reason="BT-20: after an ESP32 reset the Pi sits in the dead session for its 30 s report wait")
async def test_esp32_reset_between_end_and_ack_recovers_on_next_poll(scenario, backend, truck_route):
    """The ESP32 resets right after END: no ACK, and the RFCOMM link is never closed cleanly
    (half-open). The Pi's one-session-at-a-time accept loop keeps waiting for the ACK, so the
    rebooted ESP32's next poll gets no answer within its 3 s line timeout. The next poll must
    succeed."""
    scenario.buffer.enqueue(route_status("cam-0"))
    await scenario.dev.poll(half_open_before_ack=True)
    started = real_time.monotonic()
    result = await scenario.dev.poll()
    assert result.protocol_ok, (
        f"poll after the reset failed after {real_time.monotonic() - started:.1f} s"
    )


# --- 7. Harness ------------------------------------------------------------------------


async def test_alert_lands_within_one_poll_of_the_severity_change(scenario, backend, truck_route):
    """X-18 guard. The Pi emits a severity change at once and the ESP32 polls every 2 s, so the
    alert lands between the change and one poll later, which timing checks elsewhere (the HIL
    suite) can use as their window."""
    await scenario.frames(ND, 4)
    start = scenario.clock.now
    await scenario.frames(DM, 10)
    [(posted_at, body)] = scenario.dev.alert_posts
    assert body["severity_level"] == "medium"
    assert start <= posted_at <= start + POLL_EVERY_S + 1.0 / FPS, (
        f"posted {posted_at - start:.1f} s after the change, window [0, {POLL_EVERY_S:.1f}]"
    )
