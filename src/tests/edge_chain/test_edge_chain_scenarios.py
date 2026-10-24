"""Whole-chain scenarios: real cv-argus Orchestrator -> real Buffer/SenderServer (Bluetooth v2) ->
simulated ESP32 -> real backend-argus (POST /api/devices/me/report).

Unlike test_edge_chain.py, nothing here hand-builds Pi records: a scripted camera feeds
detections into cv-argus's real `Orchestrator` (5 fps, on a fake clock), so the ESP32 side only
ever sees what cv-argus actually emits. Each fake detection carries a rule severity the way the
parts rules do (`DetectionResult.extras`), since the firmware's events follow that severity
(C1.5; no grip sensor yet). Scenario IDs are in docs/designs/edge-cloud-scenarios.md.
"""
from __future__ import annotations

import time as real_time
from datetime import datetime
from types import SimpleNamespace

import pytest

from cv_argus.alerts import Alert, AlertKind
from cv_argus.orchestrator import Orchestrator
from cv_argus.orchestrator.parts_rules import FrameSignals, PartsRuleEngine, to_detection

from .esp32_sim import SimulatedEsp32

pytestmark = pytest.mark.asyncio

FPS = 5
POLL_EVERY_S = 2.0  # config.h PI_BT_POLL_INTERVAL_MS
# The fake wall clock's origin (for created_at_ms): an hour ago.
WALL_BASE = real_time.time() - 3600.0


class _Probs(list):
    def tolist(self):
        return list(self)


class _Det:
    def __init__(self, level, p_drowsy, severity, reason):
        self.level = level
        self.probabilities = _Probs([round(1 - p_drowsy, 4), p_drowsy])
        self.class_name = "Drowsy" if level == 2 else "Not Drowsy"
        self.extras = {"severity": severity, "reason": reason, "model": "parts-rules-v1"}


DC = _Det(2, 0.95, "critical", "eyes_closed")    # what the rules say after 3 s of closed eyes
DM = _Det(2, 0.75, "medium", "eyes_closed")      # ... after 2 s
ND = _Det(1, 0.05, "none", "ok")
D = DC
NOFACE = None

# What the camera sees, for scenarios driven through the real rule engine (Scenario.signals).
FACE_OPEN = FrameSignals(face=True, eyes_visible=True)
FACE_EYES_CLOSED = FrameSignals(face=True, eyes_visible=True, eyes_closed=True)
FACE_GONE = FrameSignals(face=False)


@pytest.fixture
def pi_clock(monkeypatch, clock):
    """Point cv-argus's clock reads (orchestrator timing, Alert stamps, the serve-time `age_s`) at
    the shared fake clock, without touching the real `time` module that queue/thread timeouts and
    the buffer's leases rely on. The Pi's clock counts as NTP-synced."""
    fake_time = SimpleNamespace(
        monotonic=lambda: clock.now,
        monotonic_ns=lambda: int(clock.now * 1e9),
        time=lambda: WALL_BASE + clock.now,
        time_ns=lambda: int((WALL_BASE + clock.now) * 1e9),
        sleep=real_time.sleep,
    )
    import cv_argus.alerts.models as alert_models
    import cv_argus.alerts.serialization as alert_serialization
    import cv_argus.orchestrator.orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module, "time", fake_time)
    monkeypatch.setattr(alert_models, "time", fake_time)
    monkeypatch.setattr(alert_serialization, "time", fake_time)
    monkeypatch.setenv("CV_ARGUS_CLOCK_SYNCED", "1")
    return clock


class Scenario:
    """Drives camera frames, the ESP32's tasks (GPS reads, the sender) and its Bluetooth polls on
    one clock."""

    def __init__(self, buffer, dev: SimulatedEsp32, clock):
        self.buffer = buffer
        self.dev = dev
        self.clock = clock
        self.orch = Orchestrator(
            buffer, debounce_frames=3, keepalive_seconds=2.0, warmup_frames=0
        )
        self.camera_alive = True
        self.esp32_polling = True
        self._next_poll = clock.now + POLL_EVERY_S
        self.rules = PartsRuleEngine()

    async def _step(self, detection, *, frame: bool):
        if self.camera_alive:
            if frame:
                self.orch._on_detection("cam-0", detection)
            self.orch._tick("cam-0")
        await self.dev.tick()
        self.clock.now += 1.0 / FPS
        if self.esp32_polling and self.clock.now >= self._next_poll:
            self._next_poll += POLL_EVERY_S
            await self.dev.poll()

    async def frames(self, detection, seconds: float):
        for _ in range(int(seconds * FPS)):
            await self._step(detection, frame=True)

    async def signals(self, signals: FrameSignals, seconds: float):
        """Frames as the camera sees them, through cv-argus's real rule engine, exactly like
        parts_stages.py: no detection only when there's no face *and* no rule active."""
        for _ in range(int(seconds * FPS)):
            state = self.rules.update(WALL_BASE + self.clock.now, signals)
            detection = (to_detection(state)
                         if signals.face or state.severity != "none" else None)
            await self._step(detection, frame=True)

    async def idle(self, seconds: float):
        """Time passes with no camera frames (camera_alive still emits keepalives)."""
        for _ in range(int(seconds * FPS)):
            await self._step(None, frame=False)

    def settled(self) -> bool:
        s = self.dev.sender
        return self.buffer.unsent_count() == 0 and not s.live and s.dlq is None and not s.outcomes

    async def drain(self, max_polls: int = 40):
        """Polls (2 s apart, the device's tasks running in between) until the Pi has nothing
        unsent and the ESP32 holds nothing."""
        for _ in range(max_polls):
            if self.settled():
                return
            for _ in range(int(POLL_EVERY_S * FPS)):
                await self.dev.tick()
                self.clock.now += 1.0 / FPS
            await self.dev.poll()
        raise AssertionError(
            f"never drained: {self.buffer.counts()}, sender live={len(self.dev.sender.live)} "
            f"dlq={self.dev.sender.dlq is not None} outcomes={len(self.dev.sender.outcomes)}"
        )


@pytest.fixture
def scenario(pi, pi_clock, device_http, truck_route):
    buffer, listener = pi
    _, key = truck_route
    dev = SimulatedEsp32(listener=listener, http=device_http, device_key=key, clock=pi_clock)
    return Scenario(buffer, dev, pi_clock)


async def alerts_on(backend, route_id):
    return (await backend.get("/api/alerts", params={"route_id": route_id})).json()


def _event_time(alert: dict) -> float:
    """AlertOut.timestamp (the event time) as epoch seconds."""
    return datetime.fromisoformat(alert["timestamp"].replace("Z", "+00:00")).timestamp()


async def test_drowsy_episode_then_recovery_gives_one_alert_and_a_driver_ok(
    scenario, backend, truck_route
):
    route_id, _ = truck_route
    await scenario.frames(ND, 4)
    await scenario.frames(DC, 10)
    await scenario.frames(ND, 15)
    await scenario.drain()

    alerts = await alerts_on(backend, route_id)
    assert [a["severity_level"] for a in alerts] == ["critical"]
    assert alerts[0]["alert_type"] == "eyes_closed"
    assert alerts[0]["driver_ok_at"] is not None
    assert alerts[0]["closed_at"] is None  # only a guardian closes it


async def test_backlog_after_30s_offline_relays_with_correct_times(scenario, backend, truck_route):
    """The Bluetooth link is down for 30 s. The event still lands at the time it happened: the
    Pi serves its age (age_s), the ESP32 adds the time it held it, and the backend subtracts."""
    route_id, _ = truck_route
    scenario.esp32_polling = False
    episode_start = scenario.clock.now
    await scenario.frames(DC, 30)
    scenario.esp32_polling = True
    await scenario.drain()

    [alert] = await alerts_on(backend, route_id)
    [post] = [p for p in scenario.dev.event_posts if p.body["event"]["kind"] == "alert"]
    age = post.body["event"]["age_s"]
    assert abs(age - (post.at - episode_start)) <= 1.5  # the event's age, as the Pi measured it
    assert abs(_event_time(alert) - (post.wall - age)) <= 1.0  # the backend's occurred_at


async def test_poison_record_does_not_block_records_behind_it(scenario, backend, truck_route):
    """A row the firmware can't parse: a NaN from the model is written as a bare NaN token, which
    ArduinoJson rejects. The firmware recovers its id from the raw line and ACKs it away."""
    route_id, _ = truck_route
    scenario.buffer.enqueue(
        Alert.new(
            AlertKind.DROWSINESS,
            level=2,
            source_id="cam-0",
            payload={"class_name": "Drowsy", "probabilities": [float("nan"), float("nan")]},
        )
    )
    await scenario.frames(DC, 6)
    await scenario.drain()
    assert [a["severity_level"] for a in await alerts_on(backend, route_id)] == ["critical"]


async def test_an_event_for_a_stopped_route_is_archived_not_retried(scenario, backend, truck_route):
    route_id, _ = truck_route
    assert (await backend.post(f"/api/routes/{route_id}/stop",
                               json={"reason": "scenario"})).status_code == 200
    await scenario.frames(DC, 6)
    await scenario.drain()
    assert await alerts_on(backend, route_id) == []
    assert scenario.buffer.counts()["archive_reasons"] == {"route_finished": 1}
    track = (await backend.get(f"/api/routes/{route_id}/track")).json()["points"]
    assert track == []  # a finished route never receives points


async def test_a_lost_report_re_serve_creates_no_duplicates(scenario, backend, truck_route):
    """The session's report lines never reach the Pi (the link drops right after END): the row
    stays unleased and is served again. The ESP32 already delivered it, so it doesn't take it
    again; its ACK rides that session (C1.8 [sync 1])."""
    route_id, _ = truck_route
    scenario.esp32_polling = False
    await scenario.frames(DC, 4)
    # Decided and delivered by the sender task, but no report line reached the Pi.
    await scenario.dev.poll(lose_report=True)
    assert [o.ack for o in scenario.dev.sender.outcomes] == [True]
    assert scenario.buffer.counts()["leased"] == 0  # the TAKEN never arrived
    scenario.esp32_polling = True
    await scenario.drain()
    assert len(await alerts_on(backend, route_id)) == 1


async def test_a_failed_driver_ok_is_retried_inside_its_window(scenario, backend, truck_route):
    route_id, _ = truck_route
    await scenario.frames(DC, 10)
    scenario.dev.backend_failures = 8  # the backend refuses right when the driver recovers
    await scenario.frames(ND, 15)
    await scenario.drain()
    [alert] = await alerts_on(backend, route_id)
    assert alert["driver_ok_at"] is not None


async def test_esp32_reboot_mid_incident_still_gets_its_driver_ok(scenario, backend, truck_route):
    """FUS-10: no NVS needed any more -- the backend matches the driver OK to the alert by
    (route, episode), whatever this device remembers."""
    route_id, _ = truck_route
    await scenario.frames(DC, 10)
    await scenario.drain()
    scenario.dev = scenario.dev.reboot()  # power loss
    await scenario.frames(ND, 15)
    await scenario.drain()
    [alert] = await alerts_on(backend, route_id)
    assert alert["driver_ok_at"] is not None


async def test_driver_slumps_out_of_frame_raises_one_no_face_critical(
    scenario, backend, truck_route
):
    """B1/CV-04 with the ladder gone: a face gone for 5 s is one no_face CRITICAL, nothing before
    it (C1.5)."""
    route_id, _ = truck_route
    await scenario.signals(FACE_OPEN, 4)
    await scenario.signals(FACE_GONE, 14)
    await scenario.drain()
    alerts = await alerts_on(backend, route_id)
    assert [(a["alert_type"], a["severity_level"]) for a in alerts] == [("no_face", "critical")]
    assert alerts[0]["driver_ok_at"] is None


async def test_escalation_is_one_incident_and_the_driver_ok_reaches_both(
    scenario, backend, truck_route
):
    route_id, _ = truck_route
    await scenario.frames(ND, 4)
    await scenario.frames(DM, 6)   # medium first ...
    await scenario.frames(DC, 6)   # ... then critical: the same episode
    await scenario.frames(ND, 15)
    await scenario.drain()

    alerts = await alerts_on(backend, route_id)
    by_severity = {a["severity_level"]: a for a in alerts}
    assert set(by_severity) == {"medium", "critical"}
    assert by_severity["critical"]["related_alert_id"] == by_severity["medium"]["id_alert"]
    assert by_severity["critical"]["id_incident"] == by_severity["medium"]["id_incident"]
    assert all(a["driver_ok_at"] is not None for a in alerts)


async def test_no_credentials_dead_letters_then_delivers_with_the_original_time(
    scenario, backend, truck_route
):
    """The Pi has no argus-device.env yet: the critical's live attempts fail `no_credentials`
    until its 60 s window runs out, then it waits in the Pi's dead-letter queue (with the event
    composed then). Once credentials arrive, the replay delivers it with the time it happened."""
    route_id, key = truck_route
    scenario.dev.device_key = None
    start = scenario.clock.now
    await scenario.frames(DC, 6)
    await scenario.idle(62)
    counts = scenario.buffer.counts()
    assert counts["dlq"] == 1 and counts["dlq_reasons"] == {"no_credentials": 1}
    assert await alerts_on(backend, route_id) == []

    scenario.dev.device_key = key  # argus-device.env installed
    await scenario.drain()
    [alert] = await alerts_on(backend, route_id)
    assert alert["delivery"] == "dlq"
    [post] = [p for p in scenario.dev.event_posts if p.status == 200]
    assert abs(post.body["event"]["age_s"] - (post.at - start)) <= 1.5
