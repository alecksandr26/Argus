"""`orchestrator/orchestrator.py` — thread start/stop/join responsiveness, against a real
thread but a fake `Buffer` and an empty input queue (no real detections needed)."""

from cv_argus.orchestrator import Orchestrator


class _FakeBuffer:
    def enqueue(self, alert):
        pass


def test_start_then_stop_join_actually_stops_the_thread():
    orch = Orchestrator(_FakeBuffer(), poll_seconds=0.05)
    orch.start()
    assert orch.is_alive
    orch.stop()
    orch.join(timeout=2.0)
    assert not orch.is_alive


def test_is_alive_false_before_start():
    orch = Orchestrator(_FakeBuffer())
    assert not orch.is_alive
