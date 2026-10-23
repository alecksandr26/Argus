"""Edge-to-cloud chain: cv-argus buffer -> Bluetooth v2 (PULL/DLQ, ACK/TAKEN/FAIL, DONE/OK) ->
ESP32 -> POST /api/devices/me/report -> backend-argus.

Real code at every hop except the Bluetooth radio (FakeTransport) and the ESP32's own C++
(stood in for by `esp32_sim.SimulatedEsp32`, a module-for-module mirror of the firmware). The
records here are hand-built the way cv-argus's Orchestrator writes them (C1.4);
test_edge_chain_scenarios.py drives the real Orchestrator instead. See this directory's README.md.
"""
from __future__ import annotations

import pytest

from cv_argus.alerts import Alert, AlertKind

from .esp32_sim import SimulatedEsp32

pytestmark = pytest.mark.asyncio


def record(severity: str, previous: str, *, transition: bool = True,
           episode: str | None = "a1b2c3d4e5f6") -> Alert:
    """What cv-argus's Orchestrator enqueues: a severity change (transition) or a keepalive."""
    p_drowsy = {"none": 0.05, "low": 0.30, "medium": 0.75, "critical": 0.95}[severity]
    drowsy = severity in ("medium", "critical")
    payload = {
        "severity": severity,
        "previous_severity": previous,
        "reason": "eyes_closed" if drowsy else "ok",
        "model": "parts-rules-v1",
        "class_name": "Drowsy" if drowsy else "Not Drowsy",
        "probabilities": [round(1 - p_drowsy, 2), p_drowsy],
        "transition": transition,
    }
    if episode:
        payload["episode"] = episode
    return Alert.new(kind=AlertKind.DROWSINESS, level=2 if drowsy else 1, source_id="cam-0",
                     payload=payload)


def keepalive(severity: str) -> Alert:
    return record(severity, severity, transition=False,
                  episode="a1b2c3d4e5f6" if severity in ("medium", "critical") else None)


def esp32(pi, device_http, truck_route, clock, **kw) -> SimulatedEsp32:
    _, listener = pi
    _, key = truck_route
    return SimulatedEsp32(listener=listener, http=device_http, device_key=key, clock=clock, **kw)


async def alerts_on(backend, route_id):
    return (await backend.get("/api/alerts", params={"route_id": route_id})).json()


async def run(dev: SimulatedEsp32, clock, seconds: float, poll_every: float = 2.0,
              until=None) -> None:
    """The device for up to `seconds` (or until `until()` holds): its 1 s GPS reads and sender
    all the time, a Bluetooth poll every `poll_every` s."""
    end = clock.now + seconds
    next_poll = clock.now
    while clock.now < end:
        if clock.now >= next_poll:
            await dev.poll()
            next_poll += poll_every
            if until is not None and until():
                return
        await dev.tick()
        clock.now = round(clock.now + 0.5, 3)
    if until is not None:
        assert until(), f"condition not met within {seconds} s"


async def test_camera_critical_becomes_one_critical_alert_and_the_recovery_a_driver_ok(
    pi, backend, device_http, truck_route, clock
):
    buffer, _ = pi
    route_id, _ = truck_route
    dev = esp32(pi, device_http, truck_route, clock)

    calm = keepalive("none")
    buffer.enqueue(calm)
    raised = record("critical", "none")
    buffer.enqueue(raised)
    result = await dev.poll()
    assert result.live.acked == [calm.id]       # a keepalive is only vigilance: ACKed
    assert result.live.taken == [raised.id]     # an event goes to the sender: TAKEN (leased)
    [alert] = await alerts_on(backend, route_id)
    assert alert["severity_level"] == "critical"
    assert alert["source"] == "fusion"
    assert alert["alert_type"] == "eyes_closed"
    assert alert["episode"] == "a1b2c3d4e5f6"
    assert alert["delivery"] == "live"
    assert alert["ai_metadata"]["model"] == "parts-rules-v1"
    assert alert["ai_metadata"]["scores"]["drowsy"] == pytest.approx(0.95)
    assert alert["coordinates"] == {"lat": 19.43, "lon": -99.13}
    assert alert["location_is_last_known"] is False
    assert alert["id_incident"]
    assert alert["driver_ok_at"] is None

    await dev.poll()  # the event's ACK rides the next session
    assert buffer.unsent_count() == 0

    # The points carry the camera's severity as their vigilance.
    buffer.enqueue(keepalive("critical"))
    await dev.poll()
    await run(dev, clock, 6)
    track = (await backend.get(f"/api/routes/{route_id}/track")).json()["points"]
    assert track and track[-1]["vigilance"] == "critical"

    # The severity falls back: one driver OK for the episode. It never closes the alert.
    buffer.enqueue(record("none", "critical"))
    await run(dev, clock, 4)
    [alert] = await alerts_on(backend, route_id)
    assert alert["driver_ok_at"] is not None
    assert alert["closed_at"] is None
    assert buffer.unsent_count() == 0


async def test_a_critical_refused_for_its_live_window_is_replayed_from_the_dlq_where_it_happened(
    pi, backend, device_http, truck_route, clock
):
    """Live first, dead-letter on failure: the backend fails (503) through the critical's 60 s live
    window; the ESP32 FAILs it to the Pi with the event it composed (attempt 1, the Pi's DLQ).
    Once the backend is back, `DLQ 1` replays it with its original position, delivery `dlq`."""
    buffer, _ = pi
    route_id, _ = truck_route
    dev = esp32(pi, device_http, truck_route, clock)
    dev.backend_failures = 10**6

    buffer.enqueue(record("critical", "none"))
    await run(dev, clock, 70, until=lambda: buffer.counts()["dlq"] == 1)
    assert clock.now >= 60  # not before its live window ran out
    [dead] = buffer.list_dlq()
    assert dead["attempts"] == 1 and dead["error"] == "http_503"
    assert dead["body"]["position"] == {"lat": 19.43, "lon": -99.13, "last_known": False,
                                        "location_age_s": 0}
    assert await alerts_on(backend, route_id) == []

    dev.backend_failures = 0
    dev.gps = (20.67, -103.35, 0.0)  # the truck moved on meanwhile
    await run(dev, clock, 6)
    [alert] = await alerts_on(backend, route_id)
    assert alert["delivery"] == "dlq"  # "recovered after lost connection"
    assert alert["coordinates"] == {"lat": 19.43, "lon": -99.13}
    [replay] = [p for p in dev.event_posts if p.status == 200]
    assert replay.body["event"]["delivery"] == "dlq"
    assert replay.body["event"]["queued_seconds"] >= 0
    assert buffer.unsent_count() == 0


async def test_dead_letters_count_every_attempt_and_archive_after_five(
    pi, backend, device_http, truck_route, clock
):
    """No network at all: the live failure is attempt 1, each `DLQ 1` replay one more; the fifth
    archives the row on the Pi (never deleted, never resent)."""
    buffer, _ = pi
    dev = esp32(pi, device_http, truck_route, clock, wifi=False)
    buffer.enqueue(record("critical", "none"))
    await run(dev, clock, 70, until=lambda: buffer.counts()["dlq"] == 1)
    assert buffer.list_dlq()[0]["attempts"] == 1
    await run(dev, clock, 60, until=lambda: buffer.counts()["archived"] == 1)
    counts = buffer.counts()
    assert (counts["dlq"], counts["archived"]) == (0, 1)
    [archived] = buffer.list_archive()
    assert archived["reason"] == "max_attempts"
    assert archived["attempts"] == 5
    assert archived["last_error"] == "no_network"


async def test_a_fail_resent_after_a_broken_session_counts_once(
    pi, backend, device_http, truck_route, clock
):
    """C1.2/C1.6 [sync 2]: report lines take effect when the Pi reads them. A session that breaks
    before OK keeps the ESP32's outcomes, so it resends the FAIL; the Pi ignores the repeat."""
    buffer, _ = pi
    dev = esp32(pi, device_http, truck_route, clock, wifi=False)
    buffer.enqueue(record("critical", "none"))
    assert (await dev.poll()).live.taken  # held by the sender, leased on the Pi
    for _ in range(70):  # until its 60 s live window runs out (no Bluetooth session meanwhile)
        if dev.sender.outcomes:
            break
        clock.now += 1.0
        await dev.tick()
    assert not dev.sender.outcomes[0].ack  # the dead-lettering FAIL, not sent yet
    dev.break_before_ok = True
    result = await dev.poll()  # the FAIL is written, then the link drops before DONE
    assert result.live.failed and not result.live.got_ok
    assert dev.sender.outcomes  # no OK: kept, to resend
    result = await dev.poll()  # the same FAIL again, then OK
    assert result.live.got_ok
    # The broken session's FAIL was applied (attempt 1, now in the DLQ); the resent one was
    # ignored and not counted (a non-leased DLQ row, C1.6 [sync 2]).
    assert result.live.summary["failed"] == 0
    assert buffer.list_dlq()[0]["attempts"] == 1


async def test_without_a_position_the_event_says_so_and_a_liveness_report_still_goes(
    pi, backend, device_http, truck_route, clock
):
    buffer, _ = pi
    route_id, _ = truck_route
    dev = esp32(pi, device_http, truck_route, clock, gps=None)
    await dev.tick()
    [liveness] = dev.posts
    assert liveness.body == {"points": []}  # C2.3 [sync 2]: points: [] every 30 s
    truck_id = liveness.answer and (await backend.get(f"/api/routes/{route_id}")).json()["id_truck"]
    truck = (await backend.get(f"/api/trucks/{truck_id}")).json()
    assert truck["device_last_seen_at"] is not None

    buffer.enqueue(record("critical", "none"))
    await dev.poll()
    [alert] = await alerts_on(backend, route_id)
    assert alert["position_unknown"] is True and alert["coordinates"] is None
    assert alert["speed_at_event"] is None


async def test_an_event_after_the_route_was_stopped_is_archived_on_the_pi(
    pi, backend, device_http, truck_route, clock
):
    """C2.2 rule 4 [sync 2]: an event after the truck's last route ended is rejected
    route_finished; the ESP32 FAILs it and the Pi archives it at once, no retry."""
    buffer, _ = pi
    route_id, _ = truck_route
    assert (await backend.post(f"/api/routes/{route_id}/stop",
                               json={"reason": "edge test"})).status_code == 200
    dev = esp32(pi, device_http, truck_route, clock)
    raised = record("critical", "none")
    buffer.enqueue(raised)
    await run(dev, clock, 4)
    assert await alerts_on(backend, route_id) == []
    rejected = [p for p in dev.event_posts if p.answer["event"]["status"] == "rejected"]
    assert len(rejected) == 1 and rejected[0].answer["event"]["reason"] == "route_finished"
    [archived] = buffer.list_archive()
    assert (archived["id"], archived["reason"]) == (raised.id, "route_finished")
    assert buffer.unsent_count() == 0


async def test_a_medium_the_backend_refuses_is_dropped_after_its_30s_window(
    pi, backend, device_http, truck_route, clock
):
    buffer, _ = pi
    dev = esp32(pi, device_http, truck_route, clock)
    dev.backend_failures = 10**6
    medium = record("medium", "none")
    buffer.enqueue(medium)
    await run(dev, clock, 34)
    counts = buffer.counts()
    assert counts["dlq"] == 0  # the DLQ holds criticals only
    assert counts["archive_reasons"] == {"retry_window": 1}


async def test_an_event_whose_age_the_pi_cannot_tell_is_archived_too_old(
    pi, backend, device_http, truck_route, clock
):
    buffer, _ = pi
    route_id, _ = truck_route
    dev = esp32(pi, device_http, truck_route, clock)
    old = record("critical", "none")
    old.boot_id, old.mono_ms, old.clock_synced = "an-earlier-boot", 1, False
    buffer.enqueue(old)
    result = await dev.poll()
    assert result.live.failed == [(old.id, "too_old")]
    assert buffer.list_archive()[0]["reason"] == "too_old"
    assert await alerts_on(backend, route_id) == []
