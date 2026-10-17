"""`Orchestrator`'s reading policy: what cv-argus must put in the buffer for the ESP32's fusion.

The ESP32's drowsy+grip fusion (`fusion_contract.py`, ported to `fusion.cpp`) needs a stream of
readings to debounce (5 s), escalate (20 s) and recover (10 s). The agreed policy: report the
camera's class **on every change** (with N-frame hysteresis in both directions) plus a
**keepalive every `keepalive_seconds`** while it stays the same, including `Not Drowsy` and
`No Face`. Today the orchestrator only emits a `Drowsy` alert at most every 30 s (cooldown) and
never `Not Drowsy`, so incidents can't recover (CV-01..04, Top #1 in
docs/designs/edge-cloud-scenarios.md).

Driven synchronously like test_orchestrator_decision.py: `_on_detection()` per frame, `_tick()`
for a poll timeout, `time.monotonic` patched.
"""
from __future__ import annotations

import pytest

from cv_argus.alerts import AlertKind
from cv_argus.orchestrator import Orchestrator



class _FakeBuffer:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, alert):
        self.enqueued.append(alert)

    def readings(self):
        return [a for a in self.enqueued if a.kind == AlertKind.DROWSINESS]


class _Probs(list):
    def tolist(self):
        return list(self)


class _Det:
    _NAMES = {1: "Not Drowsy", 2: "Drowsy"}

    def __init__(self, level, probabilities):
        self.level = level
        self.probabilities = _Probs(probabilities)

    @property
    def class_name(self):
        return self._NAMES[self.level]


def D(p=0.8):
    return _Det(2, [round(1 - p, 4), p])


def ND(p=0.1):
    return _Det(1, [round(1 - p, 4), p])


NOFACE = None


class _Clock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr("time.monotonic", c)
    return c


def _orch(**overrides):
    buf = _FakeBuffer()
    kwargs = dict(
        debounce_frames=3,
        keepalive_seconds=2.0,
        heartbeat_interval_seconds=60.0,
        warmup_frames=0,
    )
    kwargs.update(overrides)
    return Orchestrator(buf, **kwargs), buf


def _feed(orch, clock, detections, every=0.2, source="cam0"):
    """Feed frames at 5 fps (0.2 s apart), the deployed SAMPLE_FPS."""
    for det in detections:
        orch._on_detection(source, det)
        clock.now += every


def _classes(buf):
    return [r.payload["class_name"] for r in buf.readings()]


def test_steady_not_drowsy_emits_initial_reading_then_keepalives(clock):
    orch, buf = _orch()
    _feed(orch, clock, [ND()] * 3)
    [first] = buf.readings()
    assert (first.level, first.payload["class_name"]) == (1, "Not Drowsy")
    assert first.payload["transition"] is True

    clock.now += 2.0
    orch._tick("cam0")
    assert _classes(buf) == ["Not Drowsy", "Not Drowsy"]
    assert buf.readings()[-1].payload["transition"] is False


def test_drowsy_after_3_frames_emits_one_transition(clock):
    orch, buf = _orch(keepalive_seconds=999.0)
    _feed(orch, clock, [ND()] * 3)
    _feed(orch, clock, [D(), D()])
    assert _classes(buf) == ["Not Drowsy"]
    _feed(orch, clock, [D()])
    assert _classes(buf) == ["Not Drowsy", "Drowsy"]
    assert buf.readings()[-1].level == 2
    _feed(orch, clock, [D()] * 10)
    assert _classes(buf) == ["Not Drowsy", "Drowsy"]  # no repeat transition


def test_not_drowsy_after_3_frames_emits_falling_transition(clock):
    orch, buf = _orch(keepalive_seconds=999.0)
    _feed(orch, clock, [D()] * 3)
    _feed(orch, clock, [ND(), ND()])
    assert _classes(buf) == ["Drowsy"]
    _feed(orch, clock, [ND()])
    assert _classes(buf) == ["Drowsy", "Not Drowsy"]
    assert buf.readings()[-1].payload["transition"] is True


def test_flicker_shorter_than_hysteresis_emits_nothing_new(clock):
    orch, buf = _orch(keepalive_seconds=999.0)
    _feed(orch, clock, [ND()] * 3)
    _feed(orch, clock, [D(), D(), ND(), D(), D(), ND(), D(), ND()])
    assert _classes(buf) == ["Not Drowsy"]


def test_keepalive_repeats_state_with_fresh_probabilities(clock):
    orch, buf = _orch()
    _feed(orch, clock, [D(0.8)] * 3)
    _feed(orch, clock, [D(0.93)])
    clock.now += 2.0
    orch._tick("cam0")
    last = buf.readings()[-1]
    assert last.payload["class_name"] == "Drowsy"
    assert last.payload["probabilities"] == pytest.approx([0.07, 0.93])


def test_continuing_drowsy_episode_is_never_silent_for_more_than_keepalive(clock):
    """Replaces the 30 s cooldown: fusion needs to see the episode is still going."""
    orch, buf = _orch()
    start = clock.now
    _feed(orch, clock, [D()] * 300)  # 60 s of Drowsy at 5 fps
    times = [r.mono_ms / 1000.0 for r in buf.readings()]
    assert times, "no readings at all"
    gaps = [b - a for a, b in zip([start] + times, times + [clock.now])]
    assert max(gaps[1:]) <= 2.0 + 0.2 + 1e-6


def test_keepalive_also_fires_from_tick_when_frames_stop_arriving(clock):
    orch, buf = _orch()
    _feed(orch, clock, [ND()] * 3)
    for _ in range(5):  # pipeline stalls; only poll timeouts wake the loop
        clock.now += 1.0
        orch._tick("cam0")
    assert len(buf.readings()) >= 3


def test_none_detections_report_no_face_after_hysteresis(clock):
    orch, buf = _orch(keepalive_seconds=999.0)
    _feed(orch, clock, [ND()] * 3)
    _feed(orch, clock, [NOFACE] * 3)
    last = buf.readings()[-1]
    assert last.payload["class_name"] == "No Face"
    assert last.level is None
    assert last.payload["probabilities"] is None


def test_no_face_during_drowsy_episode_never_reports_not_drowsy(clock):
    """A slumped driver often leaves the frame: that must not read as recovery."""
    orch, buf = _orch()
    _feed(orch, clock, [D()] * 3)
    _feed(orch, clock, [NOFACE] * 50)
    assert "Not Drowsy" not in _classes(buf)
    assert _classes(buf)[0] == "Drowsy"


def test_no_readings_during_warm_up_window(clock):
    orch, buf = _orch(warmup_frames=10)
    _feed(orch, clock, [D()] * 9)
    assert buf.readings() == []
    _feed(orch, clock, [D()] * 4)
    assert _classes(buf) == ["Drowsy"]


def test_two_sources_keep_independent_state(clock):
    orch, buf = _orch(keepalive_seconds=999.0)
    for _ in range(3):
        orch._on_detection("cam0", D())
        orch._on_detection("cam1", ND())
        clock.now += 0.2
    by_source = {(r.source_id, r.payload["class_name"]) for r in buf.readings()}
    assert ("cam0", "Drowsy") in by_source
    assert ("cam1", "Not Drowsy") in by_source


def test_heartbeat_still_every_60s_alongside_readings(clock):
    orch, buf = _orch()
    _feed(orch, clock, [ND()] * 3)
    for _ in range(61):
        clock.now += 1.0
        orch._tick("cam0")
    heartbeats = [a for a in buf.enqueued if a.kind == AlertKind.ROUTE_STATUS]
    assert len(heartbeats) == 1
