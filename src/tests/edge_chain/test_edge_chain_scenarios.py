"""Whole-chain scenarios: real cv-argus Orchestrator -> real Buffer/SenderServer -> simulated
ESP32 -> real backend-argus.

Unlike test_edge_chain.py, nothing here hand-builds Pi records: a scripted camera feeds
detections into cv-argus's real `Orchestrator` (5 fps, on a fake clock), so the ESP32 side only
ever sees what cv-argus actually emits. The old hand-built records were things cv-argus can't
produce (Not Drowsy readings, Drowsy readings 6 s apart), which hid the biggest bug in the
chain (X-13, Top #1 in docs/designs/edge-cloud-scenarios.md).

Every scenario fails today and is xfail(strict=True) with its scenario ID; esp32_sim.py has to
mirror the firmware fixes (record timestamps, local grip tick, ACK policy, owed resolve, NVS
snapshot, cell location, panic queue) for them to pass.
"""
from __future__ import annotations

import time as real_time
from types import SimpleNamespace

import pytest

from app.models.alert import Alert as BackendAlert
from app.models.status_route import StatusRoute
from cv_argus.alerts import Alert, AlertKind, route_status
from cv_argus.orchestrator import Orchestrator
from cv_argus.orchestrator.fusion_contract import GripState

from .esp32_sim import SimulatedEsp32

pytestmark = pytest.mark.asyncio

FPS = 5
POLL_EVERY_S = 5.0  # config.h PI_BT_POLL_INTERVAL_MS
WALL_BASE = 1_800_000_000.0  # fake wall clock origin (2027) for created_at_ms


class _Probs(list):
    def tolist(self):
        return list(self)


class _Det:
    def __init__(self, level, p_drowsy):
        self.level = level
        self.probabilities = _Probs([round(1 - p_drowsy, 4), p_drowsy])
        self.class_name = "Drowsy" if level == 2 else "Not Drowsy"


D = _Det(2, 0.9)
ND = _Det(1, 0.1)
NOFACE = None


@pytest.fixture
def pi_clock(monkeypatch, clock):
    """Point cv-argus's clock reads (orchestrator timing, Alert stamps) at the shared fake clock,
    without touching the real `time` module that queue/thread timeouts rely on."""
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
    return clock


class Scenario:
    """Drives camera frames, the ESP32's local tick and its 5 s Bluetooth polls on one clock."""

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

    async def idle(self, seconds: float):
        """Time passes with no camera frames (camera_alive still emits keepalives)."""
        for _ in range(int(seconds * FPS)):
            await self._step(None, frame=False)

    async def drain(self, max_polls: int = 20):
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


@pytest.mark.xfail(strict=True, reason="Top #1/CV-01/FUS-*: chain can't recover an incident")
async def test_drowsy_episode_then_recovery_resolves_incident(scenario, backend, truck_route):
    route_id, _ = truck_route
    scenario.dev.grip = GripState.BAD
    await scenario.frames(ND, 4)
    await scenario.frames(D, 10)
    await scenario.frames(ND, 15)
    await scenario.drain()

    alerts = await alerts_on(backend, route_id)
    assert [a["severity_level"] for a in alerts] == ["critical"]
    assert alerts[0]["resolved_at"] is not None


@pytest.mark.xfail(strict=True, reason="FUS-01/X-03: grip is only evaluated on Pi records")
async def test_grip_released_while_not_drowsy_raises_medium(scenario, backend, truck_route):
    route_id, _ = truck_route
    scenario.dev.grip = GripState.GOOD
    await scenario.frames(ND, 10)
    scenario.dev.grip = GripState.BAD  # hand off the wheel; camera keeps saying Not Drowsy
    await scenario.frames(ND, 10)
    await scenario.drain()

    [alert] = await alerts_on(backend, route_id)
    assert alert["severity_level"] == "medium"
    assert alert["grip_status"] == "bad"
    assert alert["alert_type"] == "grip_released"  # not "Not Drowsy"


@pytest.mark.xfail(strict=True, reason="FUS-02/BE-07: a backlog is fused and stamped late")
async def test_backlog_after_30s_offline_relays_with_correct_times(
    scenario, backend, truck_route, clock
):
    route_id, _ = truck_route
    scenario.dev.grip = GripState.BAD
    scenario.esp32_polling = False  # Bluetooth link down
    episode_start = clock.now
    await scenario.frames(D, 30)
    scenario.esp32_polling = True
    await scenario.drain()

    [alert] = await alerts_on(backend, route_id)
    # Confirmed ~3 frames + 5 s debounce after the episode started, not 30 s later at relay time.
    expected = WALL_BASE + episode_start + 0.6 + 5.0
    occurred = BackendAlert.model_validate(alert).timestamp.timestamp()
    assert abs(occurred - expected) <= 2.5


@pytest.mark.xfail(strict=True, reason="Top #3/EBT-04: an unrelayable record blocks the queue")
async def test_poison_record_does_not_block_records_behind_it(scenario, backend, truck_route):
    route_id, _ = truck_route
    scenario.buffer.enqueue(
        Alert.new(AlertKind.DROWSINESS, level=2, source_id="cam-0", payload={"class_name": "Drowsy"})
    )  # a Pi bug: no probabilities
    scenario.buffer.enqueue(route_status("cam-0"))
    await scenario.drain()
    assert (await backend.get(f"/api/routes/{route_id}/status")).status_code == 200


@pytest.mark.xfail(strict=True, reason="NET-01/BE-04: a permanently rejected record is retried forever")
async def test_record_for_a_finished_route_is_dropped_and_acked(scenario, backend, truck_route):
    route_id, _ = truck_route
    await backend.put(f"/api/routes/{route_id}", json={"operative_status": "completed"})
    scenario.buffer.enqueue(route_status("cam-0"))
    await scenario.drain()
    assert await StatusRoute.find(StatusRoute.id_route == route_id).count() == 0


@pytest.mark.xfail(strict=True, reason="EBT-07/BE-06: a lost ACK duplicates records in the backend")
async def test_lost_ack_re_serve_creates_no_duplicates(scenario, backend, truck_route):
    route_id, _ = truck_route
    scenario.buffer.enqueue(route_status("cam-0"))
    scenario.dev.drop_next_ack = True  # relayed over HTTP, then the Bluetooth link drops
    await scenario.dev.poll()
    await scenario.drain()  # the Pi re-serves the same record
    assert await StatusRoute.find(StatusRoute.id_route == route_id).count() == 1


@pytest.mark.xfail(strict=True, reason="FUS-05: a failed resolve is never retried")
async def test_failed_resolve_is_retried_until_backend_shows_resolved(
    scenario, backend, truck_route
):
    route_id, key = truck_route
    scenario.dev.grip = GripState.BAD
    await scenario.frames(D, 10)
    scenario.dev.grip = GripState.GOOD
    scenario.dev.device_key = "wrong-key"  # backend unreachable right when recovery confirms
    await scenario.frames(ND, 15)
    scenario.dev.device_key = key
    await scenario.frames(ND, 10)
    await scenario.drain()

    [alert] = await alerts_on(backend, route_id)
    assert alert["resolved_at"] is not None


@pytest.mark.xfail(strict=True, reason="FUS-10: an ESP32 reboot loses the open incident")
async def test_esp32_reboot_mid_incident_still_resolves(scenario, backend, truck_route):
    route_id, _ = truck_route
    scenario.dev.grip = GripState.BAD
    await scenario.frames(D, 10)
    scenario.dev = scenario.dev.reboot()  # power loss; state restored from NVS
    scenario.dev.grip = GripState.GOOD
    await scenario.frames(ND, 15)
    await scenario.drain()

    [alert] = await alerts_on(backend, route_id)
    assert alert["resolved_at"] is not None


@pytest.mark.xfail(strict=True, reason="GPS-01: no GPS fix freezes the queue; no cell fallback")
async def test_no_gps_fix_uses_cell_location_marked_cell(scenario, backend, truck_route):
    route_id, _ = truck_route
    scenario.dev.gps = None
    scenario.dev.cell = (20.66, -103.37, 550)
    scenario.buffer.enqueue(route_status("cam-0"))
    await scenario.drain()
    status = (await backend.get(f"/api/routes/{route_id}/status")).json()
    assert status["location_source"] == "cell"
    assert status["location_accuracy_m"] == 550


@pytest.mark.xfail(strict=True, reason="CV-07: stale camera data keeps driving fusion")
async def test_pi_goes_silent_esp32_stops_raising_from_stale_data(scenario, backend, truck_route):
    route_id, _ = truck_route
    scenario.dev.grip = GripState.GOOD
    await scenario.frames(D, 2)  # a Drowsy reading arrives, then the Pi crashes
    scenario.camera_alive = False
    scenario.dev.grip = GripState.BAD
    await scenario.idle(30)
    assert await alerts_on(backend, route_id) == []
    assert scenario.dev.camera_offline is True


@pytest.mark.xfail(strict=True, reason="CV-04: no-face frames read as 'not drowsy'")
async def test_driver_slumps_out_of_frame_raises_an_alert(scenario, backend, truck_route):
    route_id, _ = truck_route
    scenario.dev.grip = GripState.GOOD
    await scenario.frames(ND, 4)
    await scenario.frames(NOFACE, 20)
    await scenario.drain()
    alerts = await alerts_on(backend, route_id)
    assert alerts and alerts[0]["alert_type"] == "no_face"


@pytest.mark.xfail(strict=True, reason="PAN-03/PAN-05: the simulated ESP32 has no panic path yet")
async def test_panic_during_open_incident_creates_separate_critical(
    scenario, backend, truck_route
):
    route_id, _ = truck_route
    scenario.dev.grip = GripState.BAD
    await scenario.frames(D, 10)
    await scenario.dev.press_panic()
    await scenario.drain()
    alerts = await alerts_on(backend, route_id)
    sources = sorted(a["source"] for a in alerts)
    assert sources == ["fusion", "panic_button"]


@pytest.mark.xfail(strict=True, reason="BE-12/FUS-*: escalation chain isn't fully resolved")
async def test_escalation_chain_links_and_everything_resolves(scenario, backend, truck_route):
    route_id, _ = truck_route
    scenario.dev.grip = GripState.GOOD  # drowsy + good grip = medium, escalates after 20 s
    await scenario.frames(D, 30)
    await scenario.frames(ND, 15)
    await scenario.drain()

    alerts = await alerts_on(backend, route_id)
    by_severity = {a["severity_level"]: a for a in alerts}
    assert set(by_severity) == {"medium", "critical"}
    assert by_severity["critical"]["related_alert_id"] == by_severity["medium"]["id_alert"]
    assert all(a["resolved_at"] is not None for a in alerts)
