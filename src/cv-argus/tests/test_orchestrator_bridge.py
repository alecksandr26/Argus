"""`orchestrator/bridge.py` — `OrchestratorBridgeOutputStage`'s push + drop-on-full behavior.

Uses a plain stand-in for `ctx.detection` rather than a real `DetectionResult` -- the bridge
stage forwards it opaquely without inspecting its shape (see `bridge.py`'s docstring), so a real
`DetectionResult` (which would pull in `cv_argus.model`'s tensorflow dependency just to build a
fake value) isn't needed here.
"""

import queue

from cv_argus.orchestrator import OrchestratorBridgeOutputStage
from cv_argus.pipeline import FrameContext


class _FakeFrame:
    """Minimal stand-in for a numpy frame array -- FrameContext.frame_bgr is never read by the
    bridge stage, so it doesn't need to actually be one."""


def _ctx(source_id="cam0", detection=None):
    return FrameContext(
        frame_bgr=_FakeFrame(),
        timestamp_ms=0,
        source_id=source_id,
        detection=detection,
    )


def test_handle_pushes_source_id_and_detection():
    q = queue.Queue()
    stage = OrchestratorBridgeOutputStage(q)
    fake_detection = object()
    stage.handle(_ctx(source_id="cam0", detection=fake_detection))
    assert q.get_nowait() == ("cam0", fake_detection)


def test_handle_pushes_none_detection_too():
    q = queue.Queue()
    stage = OrchestratorBridgeOutputStage(q)
    stage.handle(_ctx(source_id="cam0", detection=None))
    assert q.get_nowait() == ("cam0", None)


def test_full_queue_drops_silently_rather_than_blocking_or_raising():
    q = queue.Queue(maxsize=1)
    stage = OrchestratorBridgeOutputStage(q)
    stage.handle(_ctx())
    stage.handle(_ctx())  # queue is now full -- must not raise or block
    assert q.qsize() == 1
