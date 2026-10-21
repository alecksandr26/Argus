"""Edge-to-cloud chain: cv-argus buffer -> Bluetooth PULL/FAIL/ACK/DONE -> ESP32 -> backend-argus.

Real code at every hop except the Bluetooth radio (FakeTransport) and the ESP32's own C++
(stood in for by `esp32_sim.SimulatedEsp32`, a step-for-step mirror of the firmware's relay
loop). The records here are hand-built the way cv-argus's Orchestrator writes a severity change;
test_edge_chain_scenarios.py drives the real Orchestrator instead. See this directory's README.md.
"""
from __future__ import annotations

import pytest

from cv_argus.alerts import Alert, AlertKind, route_status

from .esp32_sim import SimulatedEsp32

pytestmark = pytest.mark.asyncio


def severity_change(severity: str, previous: str, episode: str = "ep1") -> Alert:
    """What cv-argus's Orchestrator enqueues when the rules' severity changes."""
    p_drowsy = {"none": 0.05, "low": 0.30, "medium": 0.75, "critical": 0.95}[severity]
    drowsy = severity in ("medium", "critical")
    return Alert.new(
        kind=AlertKind.DROWSINESS,
        level=2 if drowsy else 1,
        source_id="cam-0",
        payload={
            "severity": severity,
            "previous_severity": previous,
            "episode": episode,
            "reason": "eyes_closed" if drowsy else "ok",
            "model": "parts-rules-v1",
            "class_name": "Drowsy" if drowsy else "Not Drowsy",
            "probabilities": [round(1 - p_drowsy, 2), p_drowsy],
            "transition": True,
        },
    )


def esp32(pi, device_http, truck_route, clock, **kw) -> SimulatedEsp32:
    _, listener = pi
    route_id, key = truck_route
    return SimulatedEsp32(
        listener=listener, http=device_http, device_key=key, route_id=route_id, clock=clock, **kw
    )


async def alerts_on(backend, route_id):
    return (await backend.get("/api/alerts", params={"route_id": route_id})).json()


async def test_camera_critical_becomes_one_critical_alert_then_resolves(
    pi, backend, device_http, truck_route, clock
):
    buffer, _ = pi
    route_id, _ = truck_route
    dev = esp32(pi, device_http, truck_route, clock)

    buffer.enqueue(severity_change("critical", "none"))
    result = await dev.poll()
    assert (result.pulled, len(result.acked), result.failed) == (1, 1, [])
    [alert] = await alerts_on(backend, route_id)
    assert alert["severity_level"] == "critical"
    assert alert["source"] == "fusion"
    assert alert["alert_type"] == "eyes_closed"
    assert alert["grip_status"] == "good"  # placeholder until the grip sensor exists
    assert alert["ai_metadata"]["model"] == "parts-rules-v1"
    assert alert["ai_metadata"]["scores"]["drowsy"] == pytest.approx(0.95)
    assert alert["coordinates"] == {"lat": 19.43, "lon": -99.13}
    assert alert["resolved_at"] is None
    assert buffer.unsent_count() == 0

    # A heartbeat carries the camera's current severity as the route's live vigilance.
    buffer.enqueue(route_status("cam-0"))
    await dev.poll()
    status = (await backend.get(f"/api/routes/{route_id}/status")).json()
    assert status["vigilance"] == "critical"

    # The severity falls back: the episode's alert is resolved (POST .../resolve).
    clock.now = 10.0
    buffer.enqueue(severity_change("none", "critical"))
    await dev.poll()
    [alert] = await alerts_on(backend, route_id)
    assert alert["resolved_at"] is not None
    assert buffer.unsent_count() == 0


async def test_a_rejected_record_is_dead_lettered_and_delivered_on_replay(
    pi, backend, device_http, truck_route, clock
):
    """Only failed records are queued on the Pi: a 401 comes back as FAIL, the Pi keeps the
    record (with the message composed) in its dead-letter queue, and once the key works the
    replay delivers it."""
    buffer, _ = pi
    route_id, _ = truck_route
    dev = esp32(pi, device_http, truck_route, clock)
    dev.device_key = "wrong-key"

    buffer.enqueue(route_status("cam-0"))
    result = await dev.poll()
    assert (result.pulled, result.acked) == (1, [])
    assert [reason for _, reason in result.failed] == ["http_401"]
    counts = buffer.counts()
    assert (counts["outbox"], counts["dlq"]) == (0, 1)

    dev.device_key = truck_route[1]
    result = await dev.poll()
    assert (result.pulled, result.replayed, len(result.acked)) == (0, 1, 1)
    assert buffer.unsent_count() == 0
    assert (await backend.get(f"/api/routes/{route_id}/status")).status_code == 200


async def test_no_gps_fix_dead_letters_alerts_and_skips_heartbeats(
    pi, backend, device_http, truck_route, clock
):
    buffer, _ = pi
    dev = esp32(pi, device_http, truck_route, clock, gps=None)
    alert = severity_change("critical", "none")
    buffer.enqueue(alert)
    buffer.enqueue(route_status("cam-0"))
    result = await dev.poll()
    assert result.failed == [(alert.id, "no_gps")]  # an alert needs a position: kept
    assert len(result.acked) == 1                     # a heartbeat without one is skipped
    assert buffer.counts()["dlq"] == 1


async def test_alert_survives_a_failed_post_and_is_delivered_with_its_original_position(
    pi, backend, device_http, truck_route, clock
):
    """The backend refuses exactly when the camera says critical. The alert is not lost: it
    waits in the Pi's dead-letter queue with the message composed at that moment, and is
    delivered with the position where it happened, not where the truck is at replay time."""
    buffer, _ = pi
    route_id, key = truck_route
    dev = esp32(pi, device_http, truck_route, clock)

    buffer.enqueue(severity_change("critical", "none"))
    dev.device_key = "wrong-key"
    result = await dev.poll()
    assert result.acked == [] and buffer.counts()["dlq"] == 1

    dev.device_key = key
    dev.gps = (20.67, -103.35, 0.0)  # the truck moved on
    await dev.poll()
    [alert] = await alerts_on(backend, route_id)
    assert alert["severity_level"] == "critical"
    assert alert["coordinates"] == {"lat": 19.43, "lon": -99.13}
    assert buffer.unsent_count() == 0
