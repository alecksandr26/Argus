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


# --- Resilience (CV-10, BUF-04) ------------------------------------------------------------------

import sqlite3  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402


class _FailingOnceBuffer:
    """First enqueue raises like a full disk / locked DB would; later ones work."""

    def __init__(self):
        self.failures = 1
        self.enqueued = []

    def enqueue(self, alert):
        if self.failures:
            self.failures -= 1
            raise sqlite3.OperationalError("database or disk is full")
        self.enqueued.append(alert)


@pytest.mark.xfail(strict=True, reason="CV-10: an enqueue error kills the orchestrator thread")
def test_enqueue_error_does_not_kill_the_orchestrator_thread():
    buf = _FailingOnceBuffer()
    orch = Orchestrator(buf, poll_seconds=0.02, heartbeat_interval_seconds=0.05)
    orch.start()
    try:
        deadline = time.monotonic() + 3.0
        while not buf.enqueued and time.monotonic() < deadline:
            time.sleep(0.02)
        assert orch.is_alive
        assert buf.enqueued, "nothing enqueued after the first failure"
    finally:
        orch.stop()
        orch.join(timeout=2.0)


def test_stop_while_detections_are_queued_joins_cleanly():
    orch = Orchestrator(_FakeBuffer(), poll_seconds=0.05)
    orch.start()
    for _ in range(10):
        orch.input_queue.put_nowait(("cam0", None))
    orch.stop()
    orch.join(timeout=2.0)
    assert not orch.is_alive
