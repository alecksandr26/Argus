"""Whole-chain edge cases from the chain audit: real cv-argus Orchestrator -> real Buffer/
SenderServer (Bluetooth v2) -> simulated ESP32 -> real backend-argus, same harness as
test_edge_chain_scenarios.py. Scenario IDs are in docs/designs/edge-cloud-scenarios.md.

These are guards that pass today and must keep passing. A known bug would be
xfail(strict=True, raises=AssertionError), so it can only count as a known failure by failing its
own assertion, never by crashing in the harness.
"""
from __future__ import annotations

import time as real_time
from datetime import datetime, timedelta, timezone

import pytest

from cv_argus.alerts import Alert, AlertKind

from .test_edge_chain_scenarios import (  # noqa: F401  (pi_clock/scenario are fixtures)
    FACE_EYES_CLOSED,
    FACE_GONE,
    FACE_OPEN,
    DC,
    DM,
    FPS,
    ND,
    POLL_EVERY_S,
    WALL_BASE,
    _event_time,
    alerts_on,
    pi_clock,
    scenario,
)

pytestmark = pytest.mark.asyncio


async def _confirm_medium_incident(scenario):
    """The camera's severity at medium until a medium alert is created."""
    await scenario.frames(ND, 4)
    await scenario.frames(DM, 6)
    assert [a["severity"] for _, a in scenario.dev.alert_posts] == ["medium"]


async def _track(backend, route_id) -> list[dict]:
    return (await backend.get(f"/api/routes/{route_id}/track")).json()["points"]


# --- 1. What the camera sends ------------------------------------------------------------


async def test_not_drowsy_after_an_incident_gives_its_driver_ok(scenario, backend, truck_route):
    """Control for REC-18a: the same incident, followed by real Not Drowsy frames, does get its
    driver OK in this harness, so the No Face test below can only fail for the No Face reason."""
    route_id, _ = truck_route
    await _confirm_medium_incident(scenario)
    await scenario.frames(ND, 30)
    await scenario.drain()
    [alert] = await alerts_on(backend, route_id)
    assert alert["driver_ok_at"] is not None


async def test_no_face_mid_incident_does_not_recover(scenario, backend, truck_route):
    """REC-18a/B1: the driver's eyes close into critical, then they slump out of frame. Without a
    face nothing may count as a recovery; only the face back and clear gives the driver OK."""
    route_id, _ = truck_route
    await scenario.signals(FACE_OPEN, 2)
    await scenario.signals(FACE_EYES_CLOSED, 4)
    await scenario.signals(FACE_GONE, 30)
    await scenario.drain()
    alerts = await alerts_on(backend, route_id)
    assert alerts and all(a["driver_ok_at"] is None for a in alerts), "a slumped driver recovered"

    await scenario.signals(FACE_OPEN, 6)  # back, eyes open
    await scenario.drain()
    assert any(a["driver_ok_at"] is not None for a in await alerts_on(backend, route_id))


async def test_unsynced_pi_clock_never_becomes_the_event_time(scenario, backend, truck_route):
    """REC-20 guard. A Pi with no battery-backed clock that boots offline stamps rows with its
    last shutdown time and says so (`clock_synced: false`). The event time comes from the row's
    age (monotonic, same boot: C1.4), never from that `created_at_ms` (here 3 days old)."""
    route_id, _ = truck_route
    row = Alert.new(AlertKind.DROWSINESS, level=2, source_id="cam-0", payload={
        "class_name": "Drowsy", "probabilities": [0.05, 0.95], "transition": True,
        "severity": "critical", "previous_severity": "none", "episode": "0123456789ab",
        "reason": "eyes_closed"})
    row.created_at_ms = int((WALL_BASE - 3 * 24 * 3600) * 1000)
    row.clock_synced = False
    scenario.buffer.enqueue(row)
    await scenario.drain()
    [alert] = await alerts_on(backend, route_id)
    assert abs(datetime.now(timezone.utc).timestamp() - _event_time(alert)) < 120


async def test_two_cameras_do_not_reset_each_others_alerts(scenario, backend, truck_route):
    """cam-0 sees a drowsy driver while cam-1 (e.g. a second angle) sees Not Drowsy. The Pi keeps
    each camera's severity (and episode) apart, so cam-0's critical incident is still raised."""
    route_id, _ = truck_route
    for _ in range(int(20 * FPS)):
        scenario.orch._on_detection("cam-0", DC)
        scenario.orch._on_detection("cam-1", ND)
        scenario.orch._tick("cam-0")
        await scenario.dev.tick()
        scenario.clock.now += 1.0 / FPS
        if scenario.clock.now >= scenario._next_poll:
            scenario._next_poll += POLL_EVERY_S
            await scenario.dev.poll()
    await scenario.drain()
    severities = [a["severity_level"] for a in await alerts_on(backend, route_id)]
    assert "critical" in severities, f"alerts raised: {severities}"


# --- 4. Data correctness after a backlog -------------------------------------------------


async def test_backlog_replay_keeps_the_position_where_it_happened(scenario, backend, truck_route):
    """The network is down while the truck moves. The critical's live window runs out, so the
    ESP32 hands it to the Pi's DLQ with the event it composed (the position where it happened),
    and the replay sends exactly that, not the position at replay time (FUS-15a). The points of
    the failed reports are dropped (user decision): the path has a gap there."""
    route_id, _ = truck_route
    scenario.dev.wifi = False
    scenario.dev.gps = (19.40, -99.10, 80.0)
    await scenario.frames(DC, 4)
    for i in range(1, 7):  # the truck moves on for a minute and more
        scenario.dev.gps = (19.40 + i * 0.05, -99.10 - i * 0.05, 80.0)
        await scenario.idle(11)
    assert scenario.buffer.counts()["dlq"] == 1
    scenario.dev.wifi = True
    await scenario.drain()
    [alert] = await alerts_on(backend, route_id)
    assert alert["coordinates"] == {"lat": 19.40, "lon": -99.10}
    assert alert["delivery"] == "dlq"
    # Only points read after the network came back (the truck parked at its last position).
    assert {round(p["lat"], 2) for p in await _track(backend, route_id)} == {19.70}


async def test_points_keep_the_vigilance_of_their_read(scenario, backend, truck_route):
    """Each point says what the camera said when it was read (FUS-15b): calm points stay `low`
    even when they reach the backend together with the critical that followed them."""
    route_id, _ = truck_route
    await scenario.frames(ND, 8)
    await scenario.frames(DC, 10)
    await scenario.drain()
    # In read order, from what was posted (the fake clock runs far ahead of the real one, so the
    # backend's absolute timestamps of points from different reports can collide).
    read = sorted((post.at - p["age_s"], p["vigilance"]) for post in scenario.dev.posts
                  if post.status == 200 for p in post.body["points"])
    vigilance = [v for _, v in read]
    assert "low" in vigilance and "critical" in vigilance
    assert vigilance.index("low") < vigilance.index("critical")
    assert "low" not in vigilance[vigilance.index("critical"):]
    assert await _track(backend, route_id)


# --- 5. Route lifecycle ----------------------------------------------------------------


async def test_route_change_mid_incident_has_no_stuck_records(scenario, backend, truck_route):
    """An incident is open when the operator completes the route and starts the truck's next
    one (NET-12). Events are routed by time and points to the active route, so nothing gets
    stuck: the queue drains and the new route gets the points."""
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
            # Departed (by the fake clock's reckoning) before the events that follow.
            "actual_departure": (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),
            "operative_status": "in_progress",
        },
    )
    assert new.status_code == 201
    new_id = new.json()["id_route"]
    await scenario.frames(DC, 22)  # still drowsy: escalates to critical
    await scenario.frames(ND, 12)  # then recovers
    await scenario.drain(max_polls=5)  # AssertionError if records are stuck
    assert await _track(backend, new_id)
    assert scenario.dev.sender.route == "active"


# --- 6. Connectivity -------------------------------------------------------------------


async def test_esp32_reset_between_end_and_report_recovers_on_next_poll(
    scenario, backend, truck_route
):
    """The ESP32 resets right after END: no report, and the RFCOMM link is never closed cleanly
    (half-open). The Pi serves one session at a time, so it used to sit in the dead one for its
    whole report wait (BT-20). It now notices the rebooted ESP32's new connection waiting and
    drops the old session, so the next poll succeeds."""
    await scenario.frames(ND, 2)
    await scenario.dev.poll(half_open_before_report=True)
    started = real_time.monotonic()
    result = await scenario.dev.poll()
    assert result.protocol_ok and result.live.got_ok, (
        f"poll after the reset failed after {real_time.monotonic() - started:.1f} s"
    )


# --- 7. Harness ------------------------------------------------------------------------


async def test_alert_lands_within_one_poll_of_the_severity_change(scenario, backend, truck_route):
    """X-18 guard. The Pi emits a severity change at once, the ESP32 polls every 2 s and its
    sender sends an event at once, so the alert lands between the change and one poll later."""
    await scenario.frames(ND, 4)
    start = scenario.clock.now
    await scenario.frames(DM, 10)
    [(posted_at, event)] = scenario.dev.alert_posts
    assert event["severity"] == "medium"
    assert start <= posted_at <= start + POLL_EVERY_S + 1.0 / FPS, (
        f"posted {posted_at - start:.1f} s after the change, window [0, {POLL_EVERY_S:.1f}]"
    )
