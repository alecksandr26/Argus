"""`orchestrator/orchestrator.py` — the heartbeat side of the decision loop, called directly (not
through threads/queues) for determinism, against a fake `Buffer` that just records `enqueue()`
calls. The reading policy (class changes, keepalives, No Face, warm-up) is in
test_orchestrator_readings.py; the old cooldown/rising-edge-only alert tests were replaced by it.
"""

from cv_argus.alerts import AlertKind
from cv_argus.orchestrator import Orchestrator


class _FakeBuffer:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, alert):
        self.enqueued.append(alert)


def _orchestrator(**overrides):
    buf = _FakeBuffer()
    kwargs = dict(debounce_frames=3, heartbeat_interval_seconds=60.0, warmup_frames=0)
    kwargs.update(overrides)
    return Orchestrator(buf, **kwargs), buf


def test_heartbeat_not_due_yet_does_not_enqueue(monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: fake_now[0])
    orch, buf = _orchestrator()
    fake_now[0] += 59.0
    orch._maybe_heartbeat("cam0")
    assert buf.enqueued == []


def test_heartbeat_due_enqueues_route_status(monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: fake_now[0])
    orch, buf = _orchestrator()
    fake_now[0] += 61.0
    orch._maybe_heartbeat("cam0")

    assert len(buf.enqueued) == 1
    assert buf.enqueued[0].kind == AlertKind.ROUTE_STATUS
    assert buf.enqueued[0].payload == {"status": "OK"}


def test_no_heartbeat_immediately_after_start(monkeypatch):
    """The heartbeat clock starts at construction: a fresh orchestrator doesn't fire one on its
    very first poll timeout."""
    monkeypatch.setattr("time.monotonic", lambda: 1000.0)
    orch, buf = _orchestrator()
    orch._tick("cam0")
    assert buf.enqueued == []
