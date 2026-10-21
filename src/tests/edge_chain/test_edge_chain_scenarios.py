"""Whole-chain scenarios: real cv-argus Orchestrator -> real Buffer/SenderServer -> simulated
ESP32 -> real backend-argus.

Unlike test_edge_chain.py, nothing here hand-builds Pi records: a scripted camera feeds
detections into cv-argus's real `Orchestrator` (5 fps, on a fake clock), so the ESP32 side only
ever sees what cv-argus actually emits. Each fake detection carries a rule severity the way the
parts rules do (`DetectionResult.extras`), since the firmware alerts on that severity (no grip
sensor yet).

Scenarios that still fail are xfail(strict=True) with their scenario ID
(docs/designs/edge-cloud-scenarios.md); the rest pass since the "live first, dead-letter on
failure" link and the Pi-severity alerts.
"""
from __future__ import annotations

import time as real_time
from types import SimpleNamespace

import pytest

from datetime import datetime

from app.models.status_route import StatusRoute
from cv_argus.alerts import Alert, AlertKind, route_status
from cv_argus.orchestrator import Orchestrator
from cv_argus.orchestrator.parts_rules import FrameSignals, PartsRuleEngine, to_detection

from .esp32_sim import SimulatedEsp32

pytestmark = pytest.mark.asyncio

FPS = 5
POLL_EVERY_S = 2.0  # config.h PI_BT_POLL_INTERVAL_MS
# The fake wall clock's origin (for created_at_ms): an hour ago, so every event time the Pi
# stamps stays inside the backend's accepted window (no more than 5 min ahead, 7 days back).
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
    """Point cv-argus's clock reads (orchestrator timing, Alert stamps) at the shared fake clock,
    without touching the real `time` module that queue/thread timeouts rely on. The Pi's clock
    counts as NTP-synced, so its timestamps are the events' times."""
    fake_time = SimpleNamespace(
        monotonic=lambda: clock.now,
        monotonic_ns=lambda: int(clock.now * 1e9),
        time=lambda: WALL_BASE + clock.now,
        time_ns=lambda: int((WALL_BASE + clock.now) * 1e9),
        sleep=real_time.sleep,
    )
    import cv_argus.alerts.models as alert_models
    import cv_argus.orchestrator.orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module, "time", fake_time)
    monkeypatch.setattr(alert_models, "time", fake_time)
    monkeypatch.setenv("CV_ARGUS_CLOCK_SYNCED", "1")
    return clock


class Scenario:
    """Drives camera frames, the ESP32's local tick and its Bluetooth polls on one clock."""

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
        self.dev.tick()
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

    async def drain(self, max_polls: int = 30):
        for _ in range(max_polls):
            if self.buffer.unsent_count() == 0:
                return
            await self.dev.poll()
        raise AssertionError(f"Pi buffer never drained: {self.buffer.unsent_count()} left")


@pytest.fixture
def scenario(pi, pi_clock, device_http, truck_route):
    buffer, listener = pi
    route_id, key = truck_route
    dev = SimulatedEsp32(
        listener=listener, http=device_http, device_key=key, route_id=route_id, clock=pi_clock
    )
    return Scenario(buffer, dev, pi_clock)


async def alerts_on(backend, route_id):
    return (await backend.get("/api/alerts", params={"route_id": route_id})).json()


def _event_time(alert: dict) -> float:
    """AlertOut.timestamp (the event time) as epoch seconds."""
    return datetime.fromisoformat(alert["timestamp"].replace("Z", "+00:00")).timestamp()


async def test_drowsy_episode_then_recovery_resolves_incident(scenario, backend, truck_route):
    route_id, _ = truck_route
    await scenario.frames(ND, 4)
    await scenario.frames(DC, 10)
    await scenario.frames(ND, 15)
    await scenario.drain()

    alerts = await alerts_on(backend, route_id)
    assert [a["severity_level"] for a in alerts] == ["critical"]
    assert alerts[0]["alert_type"] == "eyes_closed"
    assert alerts[0]["grip_status"] == "good"  # placeholder until the sensor exists
    assert alerts[0]["resolved_at"] is not None


async def test_backlog_after_30s_offline_relays_with_correct_times(
    scenario, backend, truck_route, clock
):
    route_id, _ = truck_route
    scenario.esp32_polling = False  # Bluetooth link down
    episode_start = clock.now
    await scenario.frames(DC, 30)
    scenario.esp32_polling = True
    await scenario.drain()

    [alert] = await alerts_on(backend, route_id)
    # When the camera's severity rose (the first critical frame), not 30 s later at relay time.
    expected = WALL_BASE + episode_start
    occurred = _event_time(alert)
    assert abs(occurred - expected) <= 1.5


async def test_poison_record_does_not_block_records_behind_it(scenario, backend, truck_route):
    route_id, _ = truck_route
    # A row the firmware can't parse: a NaN from the model is written as a bare NaN token, which
    # ArduinoJson rejects. The firmware recovers its id from the raw line and ACKs it away.
    scenario.buffer.enqueue(
        Alert.new(
            AlertKind.DROWSINESS,
            level=2,
            source_id="cam-0",
            payload={"class_name": "Drowsy", "probabilities": [float("nan"), float("nan")]},
        )
    )
    scenario.buffer.enqueue(route_status("cam-0"))
    await scenario.drain()
    assert (await backend.get(f"/api/routes/{route_id}/status")).status_code == 200


async def test_record_for_a_finished_route_is_dropped_and_acked(scenario, backend, truck_route):
    route_id, _ = truck_route
    await backend.put(f"/api/routes/{route_id}", json={"operative_status": "completed"})
    scenario.buffer.enqueue(route_status("cam-0"))
    await scenario.drain()
    assert await StatusRoute.find(StatusRoute.id_route == route_id).count() == 0


async def test_lost_ack_re_serve_creates_no_duplicates(scenario, backend, truck_route):
    route_id, _ = truck_route
    scenario.buffer.enqueue(route_status("cam-0"))
    scenario.dev.drop_next_ack = True  # relayed over HTTP, then the Bluetooth link drops
    await scenario.dev.poll()
    await scenario.drain()  # the Pi re-serves the same record; client_event_id dedups it
    assert await StatusRoute.find(StatusRoute.id_route == route_id).count() == 1


async def test_failed_resolve_is_retried_until_backend_shows_resolved(
    scenario, backend, truck_route
):
    route_id, key = truck_route
    await scenario.frames(DC, 10)
    scenario.dev.device_key = "wrong-key"  # the backend refuses right when the driver recovers
    await scenario.frames(ND, 15)
    assert scenario.buffer.counts()["dlq"] >= 1  # the resolve waits in the dead-letter queue
    scenario.dev.device_key = key
    await scenario.frames(ND, 10)
    await scenario.drain()

    [alert] = await alerts_on(backend, route_id)
    assert alert["resolved_at"] is not None


async def test_esp32_reboot_mid_incident_still_resolves(scenario, backend, truck_route):
    """FUS-10: the episode's alert id is kept in NVS, so the recovery after a reboot resolves."""
    route_id, _ = truck_route
    await scenario.frames(DC, 10)
    scenario.dev = scenario.dev.reboot()  # power loss
    await scenario.frames(ND, 15)
    await scenario.drain()

    [alert] = await alerts_on(backend, route_id)
    assert alert["resolved_at"] is not None


@pytest.mark.xfail(strict=True, reason="GPS-01: no cell-location fallback; a status without a "
                                       "GPS fix is skipped")
async def test_no_gps_fix_uses_cell_location_marked_cell(scenario, backend, truck_route):
    route_id, _ = truck_route
    scenario.dev.gps = None
    scenario.dev.cell = (20.66, -103.37, 550)
    scenario.buffer.enqueue(route_status("cam-0"))
    await scenario.drain()
    status = (await backend.get(f"/api/routes/{route_id}/status")).json()
    assert status["location_source"] == "cell"
    assert status["location_accuracy_m"] == 550


@pytest.mark.xfail(strict=True, reason="CV-07: no camera-offline detection on the ESP32")
async def test_pi_goes_silent_esp32_reports_the_camera_offline(scenario, backend, truck_route):
    await scenario.frames(DC, 2)  # a reading arrives, then the Pi crashes
    scenario.camera_alive = False
    await scenario.idle(30)
    assert scenario.dev.camera_offline is True


async def test_driver_slumps_out_of_frame_raises_an_alert(scenario, backend, truck_route):
    """B1/CV-04: with no alert open, a face gone for 5 s raises its own no_face alert, which
    escalates to critical at 10 s."""
    route_id, _ = truck_route
    await scenario.signals(FACE_OPEN, 4)
    await scenario.signals(FACE_GONE, 14)
    await scenario.drain()
    alerts = await alerts_on(backend, route_id)
    assert {a["alert_type"] for a in alerts} == {"no_face"}
    assert sorted(a["severity_level"] for a in alerts) == ["critical", "medium"]
    assert all(a["resolved_at"] is None for a in alerts)


async def test_panic_during_open_incident_creates_separate_critical(
    scenario, backend, truck_route
):
    route_id, _ = truck_route
    await scenario.frames(DC, 10)
    assert await scenario.dev.press_panic()
    await scenario.drain()
    alerts = await alerts_on(backend, route_id)
    sources = sorted(a["source"] for a in alerts)
    assert sources == ["fusion", "panic_button"]


async def test_panic_while_offline_is_delivered_once_the_backend_is_back(
    scenario, backend, truck_route
):
    """PAN-03: the press is queued (in NVS on the device) and retried, never lost."""
    route_id, key = truck_route
    scenario.dev.device_key = "wrong-key"
    assert not await scenario.dev.press_panic()
    await scenario.frames(ND, 4)
    assert await alerts_on(backend, route_id) == []
    scenario.dev = scenario.dev.reboot()  # and the device even reboots in between
    scenario.dev.device_key = key
    await scenario.frames(ND, 4)
    [alert] = await alerts_on(backend, route_id)
    assert (alert["source"], alert["severity_level"]) == ("panic_button", "critical")
    assert alert["coordinates"] == {"lat": 19.43, "lon": -99.13}


async def test_escalation_chain_links_and_everything_resolves(scenario, backend, truck_route):
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
    assert all(a["resolved_at"] is not None for a in alerts)


async def test_no_credentials_dead_letters_then_delivers_with_the_original_time(
    scenario, backend, truck_route, clock
):
    """The Pi has no argus-device.env yet: every backend-bound record comes back as
    `FAIL no_credentials` and waits in the Pi's dead-letter queue, while the live stream keeps
    flowing. Once credentials arrive, the replay delivers it with the time it happened."""
    route_id, key = truck_route
    scenario.dev.device_key = None
    start = clock.now
    await scenario.frames(DC, 6)
    counts = scenario.buffer.counts()
    assert counts["dlq"] >= 1 and counts["dlq_reasons"] == {"no_credentials": counts["dlq"]}
    assert await alerts_on(backend, route_id) == []

    scenario.dev.device_key = key  # argus-device.env installed
    await scenario.frames(DC, 4)
    await scenario.drain()
    [alert] = await alerts_on(backend, route_id)
    occurred = _event_time(alert)
    assert abs(occurred - (WALL_BASE + start)) <= 1.5
