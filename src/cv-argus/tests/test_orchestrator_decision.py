"""`orchestrator/orchestrator.py` — the decision-loop logic, called directly (not through
threads/queues) for determinism, against a fake `Buffer` that just records `enqueue()` calls.

Uses a plain stand-in for `DetectionResult` rather than the real class -- `Orchestrator` only
ever duck-types `.level`/`.class_name`/`.probabilities.tolist()` (see `orchestrator.py`'s module
docstring), so the real class (which would pull in `cv_argus.model`'s tensorflow dependency just
to build a fake value) isn't needed here.
"""

import pytest

from cv_argus.alerts import AlertKind
from cv_argus.orchestrator import Orchestrator


class _FakeBuffer:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, alert):
        self.enqueued.append(alert)


class _ListWithTolist(list):
    """A list that also answers `.tolist()`, matching the one bit of numpy-array-shaped API
    surface `Orchestrator` actually relies on (`detection.probabilities.tolist()`)."""

    def tolist(self):
        return list(self)


class _FakeDetection:
    _CLASS_NAMES = {1: "Not Drowsy", 2: "Drowsy"}

    def __init__(self, level, probabilities):
        self.level = level
        self.probabilities = _ListWithTolist(probabilities)

    @property
    def class_name(self):
        return self._CLASS_NAMES[self.level]


def _drowsy():
    return _FakeDetection(level=2, probabilities=[0.2, 0.8])


def _not_drowsy():
    return _FakeDetection(level=1, probabilities=[0.9, 0.1])


def _orchestrator(**overrides):
    buf = _FakeBuffer()
    kwargs = dict(
        debounce_frames=3,
        cooldown_seconds=30.0,
        heartbeat_interval_seconds=60.0,
    )
    kwargs.update(overrides)
    return Orchestrator(buf, **kwargs), buf


def test_fewer_than_debounce_frames_does_not_alert():
    orch, buf = _orchestrator()
    orch._on_detection("cam0", _drowsy())
    orch._on_detection("cam0", _drowsy())
    assert buf.enqueued == []


def test_exactly_debounce_frames_raises_one_drowsiness_alert():
    orch, buf = _orchestrator()
    for _ in range(3):
        orch._on_detection("cam0", _drowsy())
    assert len(buf.enqueued) == 1
    assert buf.enqueued[0].kind == AlertKind.DROWSINESS
    assert buf.enqueued[0].level == 2
    assert buf.enqueued[0].payload["class_name"] == "Drowsy"
    assert buf.enqueued[0].payload["probabilities"] == pytest.approx([0.2, 0.8])


def test_not_drowsy_resets_the_consecutive_counter():
    orch, buf = _orchestrator()
    orch._on_detection("cam0", _drowsy())
    orch._on_detection("cam0", _drowsy())
    orch._on_detection("cam0", _not_drowsy())  # resets
    orch._on_detection("cam0", _drowsy())
    orch._on_detection("cam0", _drowsy())
    assert buf.enqueued == []  # only 2 consecutive so far, needs a 3rd
    orch._on_detection("cam0", _drowsy())
    assert len(buf.enqueued) == 1


def test_none_detection_resets_the_consecutive_counter():
    orch, buf = _orchestrator()
    orch._on_detection("cam0", _drowsy())
    orch._on_detection("cam0", _drowsy())
    orch._on_detection("cam0", None)  # no face found -- resets
    orch._on_detection("cam0", _drowsy())
    orch._on_detection("cam0", _drowsy())
    assert buf.enqueued == []


def test_cooldown_suppresses_a_second_alert(monkeypatch):
    orch, buf = _orchestrator(cooldown_seconds=30.0)
    fake_now = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: fake_now[0])

    for _ in range(3):
        orch._on_detection("cam0", _drowsy())
    assert len(buf.enqueued) == 1

    fake_now[0] += 10.0  # still within cooldown
    for _ in range(3):
        orch._on_detection("cam0", _drowsy())
    assert len(buf.enqueued) == 1  # no second alert yet


def test_cooldown_expiring_allows_a_second_alert(monkeypatch):
    orch, buf = _orchestrator(cooldown_seconds=30.0)
    fake_now = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: fake_now[0])

    for _ in range(3):
        orch._on_detection("cam0", _drowsy())
    assert len(buf.enqueued) == 1

    fake_now[0] += 31.0  # cooldown elapsed
    for _ in range(3):
        orch._on_detection("cam0", _drowsy())
    assert len(buf.enqueued) == 2


def test_heartbeat_not_due_yet_does_not_enqueue():
    orch, buf = _orchestrator(heartbeat_interval_seconds=60.0)
    orch._last_heartbeat_at = __import__("time").monotonic()
    orch._maybe_heartbeat("cam0")
    assert buf.enqueued == []


def test_heartbeat_due_enqueues_route_status(monkeypatch):
    orch, buf = _orchestrator(heartbeat_interval_seconds=60.0)
    fake_now = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: fake_now[0])
    orch._last_heartbeat_at = fake_now[0]

    fake_now[0] += 61.0
    orch._maybe_heartbeat("cam0")

    assert len(buf.enqueued) == 1
    assert buf.enqueued[0].kind == AlertKind.ROUTE_STATUS
    assert buf.enqueued[0].payload == {"status": "OK"}


def test_an_alert_also_resets_the_heartbeat_clock(monkeypatch):
    orch, buf = _orchestrator(heartbeat_interval_seconds=60.0, cooldown_seconds=30.0)
    fake_now = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: fake_now[0])
    orch._last_heartbeat_at = fake_now[0]

    for _ in range(3):
        orch._on_detection("cam0", _drowsy())
    orch._maybe_heartbeat("cam0")  # same instant -- heartbeat must not also fire

    assert len(buf.enqueued) == 1
    assert buf.enqueued[0].kind == AlertKind.DROWSINESS
