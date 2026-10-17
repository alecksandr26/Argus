"""Edge-to-cloud chain: cv-argus buffer -> PULL/ACK -> ESP32 fusion -> backend-argus.

Real code at every hop except the Bluetooth radio (FakeTransport) and the ESP32's own C++
(stood in for by `esp32_sim.SimulatedEsp32`, a step-for-step mirror of the firmware's relay
loop). See this directory's README.md.
"""
from __future__ import annotations

import pytest

from cv_argus.alerts import Alert, AlertKind, route_status
from cv_argus.orchestrator.fusion_contract import GripState

from .esp32_sim import SimulatedEsp32

pytestmark = pytest.mark.asyncio


def drowsy(p_drowsy: float = 0.88) -> Alert:
    """What cv-argus's Orchestrator enqueues for a Drowsy detection."""
    return Alert.new(
        kind=AlertKind.DROWSINESS,
        level=2 if p_drowsy >= 0.57 else 1,
        source_id="cam-0",
        payload={
            "class_name": "Drowsy" if p_drowsy >= 0.57 else "Not Drowsy",
            "probabilities": [round(1 - p_drowsy, 4), p_drowsy],
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


async def test_drowsy_with_bad_grip_becomes_one_critical_alert_then_resolves(
    pi, backend, device_http, truck_route, clock
):
    buffer, _ = pi
    route_id, _ = truck_route
    dev = esp32(pi, device_http, truck_route, clock, grip=GripState.BAD)

    # t=0: first Drowsy reading. Fusion starts debouncing; nothing posted, but the record was
    # fully processed, so it's ACKed and leaves the Pi's buffer.
    buffer.enqueue(drowsy())
    result = await dev.poll()
    assert (result.pulled, len(result.acked), result.protocol_ok) == (1, 1, True)
    assert await alerts_on(backend, route_id) == []
    assert buffer.unsent_count() == 0

    # t=6: still Drowsy + bad grip past the 5s debounce -> one critical fusion alert.
    clock.now = 6.0
    buffer.enqueue(drowsy())
    await dev.poll()
    [alert] = await alerts_on(backend, route_id)
    assert alert["severity_level"] == "critical"
    assert alert["source"] == "fusion"
    assert alert["grip_status"] == "bad"
    assert alert["ai_metadata"]["scores"]["drowsy"] == pytest.approx(0.88)
    assert alert["coordinates"] == {"lat": 19.43, "lon": -99.13}
    assert alert["resolved_at"] is None

    # A heartbeat relays the fused (not camera-only) severity as the route's live vigilance.
    buffer.enqueue(route_status("cam-0"))
    await dev.poll()
    status = (await backend.get(f"/api/routes/{route_id}/status")).json()
    assert status["vigilance"] == "critical"

    # Driver recovers: alert + good grip, held for the 10s recovery window -> resolved.
    dev.grip = GripState.GOOD
    clock.now = 8.0
    buffer.enqueue(drowsy(p_drowsy=0.10))
    await dev.poll()
    clock.now = 19.0
    buffer.enqueue(drowsy(p_drowsy=0.10))
    await dev.poll()
    [alert] = await alerts_on(backend, route_id)
    assert alert["resolved_at"] is not None
    assert buffer.unsent_count() == 0


async def test_record_is_not_acked_while_the_backend_rejects_it(
    pi, backend, device_http, truck_route, clock
):
    """'Only ACK what you actually relayed': a failed relay leaves the row unsent on the Pi."""
    buffer, _ = pi
    route_id, _ = truck_route
    dev = esp32(pi, device_http, truck_route, clock)
    dev.device_key = "wrong-key"

    buffer.enqueue(route_status("cam-0"))
    result = await dev.poll()
    assert (result.pulled, result.acked) == (1, [])
    assert buffer.unsent_count() == 1

    # Same row is re-served on the next pull and goes through once the relay works.
    dev.device_key = truck_route[1]
    result = await dev.poll()
    assert (result.pulled, len(result.acked)) == (1, 1)
    assert buffer.unsent_count() == 0
    assert (await backend.get(f"/api/routes/{route_id}/status")).status_code == 200


async def test_no_gps_fix_holds_records_on_the_pi(pi, backend, device_http, truck_route, clock):
    buffer, _ = pi
    dev = esp32(pi, device_http, truck_route, clock, gps=None)
    buffer.enqueue(drowsy())
    result = await dev.poll()
    assert result.acked == []
    assert buffer.unsent_count() == 1


async def test_alert_survives_a_failed_post_and_is_delivered_on_retry(
    pi, backend, device_http, truck_route, clock
):
    """The backend drops out exactly when fusion confirms a critical incident. The alert must
    still reach the backend once it's reachable again, not vanish because fusion already
    considers the incident confirmed."""
    buffer, _ = pi
    route_id, key = truck_route
    dev = esp32(pi, device_http, truck_route, clock, grip=GripState.BAD)

    buffer.enqueue(drowsy())
    await dev.poll()  # t=0, debounce starts

    clock.now = 6.0
    buffer.enqueue(drowsy())
    dev.device_key = "wrong-key"  # backend unreachable / rejecting at the worst moment
    result = await dev.poll()
    assert result.acked == []
    assert buffer.unsent_count() == 1

    clock.now = 11.0
    dev.device_key = key
    await dev.poll()
    alerts = await alerts_on(backend, route_id)
    assert [a["severity_level"] for a in alerts] == ["critical"]
    assert buffer.unsent_count() == 0
