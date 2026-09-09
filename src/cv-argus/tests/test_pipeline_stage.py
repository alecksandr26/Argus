"""`pipeline/stage.py` — the threaded `Stage`/`SourceStage`/`OutputStage`/`Pipeline` runner.

This is the maintained version of what `scripts/smoke_test_pipeline.py` checks by hand: thread
lifecycle, in-order delivery, sentinel-based shutdown when a source finishes on its own,
`connect()` fan-out, the drop-oldest backpressure path, and per-item error isolation. No
`cv2`/`mediapipe`/`tensorflow` — `stage.py` deliberately stays on the numpy-only import path.
"""

import threading
import time

import numpy as np
import pytest

from cv_argus.pipeline import FrameContext, OutputStage, Pipeline, SourceStage, Stage


class _CountingSource(SourceStage):
    def __init__(self, n, delay=0.002, **kw):
        super().__init__("counting_source", **kw)
        self._n, self._delay = n, delay

    def produce(self):
        for i in range(self._n):
            yield i
            time.sleep(self._delay)


class _Doubler(Stage):
    def __init__(self, **kw):
        super().__init__("doubler", **kw)

    def process_item(self, item):
        return item * 2


class _RaiseOn(Stage):
    """Passes items through, but raises on one specific value — to check one bad frame doesn't
    take the stage's thread down."""

    def __init__(self, bad, **kw):
        super().__init__("raise_on", **kw)
        self._bad = bad

    def process_item(self, item):
        if item == self._bad:
            raise ValueError(f"boom on {item}")
        return item


class _Collector(OutputStage):
    def __init__(self, handle_delay=0.0, **kw):
        super().__init__("collector", **kw)
        self.collected = []
        self._handle_delay = handle_delay

    def handle(self, item):
        if self._handle_delay:
            time.sleep(self._handle_delay)
        self.collected.append(item)


def _run_until_done(pipeline, timeout=10.0):
    deadline = time.monotonic() + timeout
    while pipeline.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    pipeline.stop(timeout=5.0)


def test_items_flow_through_in_order():
    src, dbl, out = _CountingSource(20), _Doubler(), _Collector()
    src.connect(dbl).connect(out)
    pipe = Pipeline([src, dbl, out])
    pipe.start()
    _run_until_done(pipe)

    assert out.collected == [i * 2 for i in range(20)]


def test_pipeline_reports_not_alive_after_source_finishes():
    src, out = _CountingSource(5), _Collector()
    src.connect(out)
    pipe = Pipeline([src, out])
    pipe.start()
    _run_until_done(pipe)

    assert pipe.is_alive() is False
    assert out.collected == list(range(5))


def test_connect_fans_out_to_multiple_sinks():
    src = _CountingSource(15)
    out_a, out_b = _Collector(), _Collector()
    src.connect(out_a)
    src.connect(out_b)
    pipe = Pipeline([src, out_a, out_b])
    pipe.start()
    _run_until_done(pipe)

    assert out_a.collected == list(range(15))
    assert out_b.collected == list(range(15))


def test_error_in_process_item_does_not_kill_the_stage():
    src = _CountingSource(10)
    mid = _RaiseOn(bad=4)
    out = _Collector()
    src.connect(mid).connect(out)
    pipe = Pipeline([src, mid, out])
    pipe.start()
    _run_until_done(pipe)

    assert out.collected == [i for i in range(10) if i != 4]  # only the bad frame is dropped


def test_drop_oldest_when_full_sheds_frames_under_a_slow_sink():
    # live-source semantics: bound latency by dropping stale frames rather than blocking.
    src = _CountingSource(60, delay=0.0, drop_oldest_when_full=True)
    slow = _Collector(handle_delay=0.02)
    src.connect(slow)
    pipe = Pipeline([src, slow])
    pipe.start()
    _run_until_done(pipe, timeout=15.0)

    assert src.stats._drops > 0                     # the drop-oldest path actually fired
    assert len(slow.collected) < 60                 # and frames were genuinely shed
    assert slow.collected == sorted(slow.collected)  # what survived stayed in order


def test_no_drop_policy_does_not_shed_frames_mid_stream():
    # default (offline-file) semantics: no drop-oldest policy -> the source blocks on a full
    # queue rather than discarding, so nothing is lost while the stream is running. (Shutdown
    # can still drop the single item queued behind a full buffer when the sentinel is inserted
    # -- a different code path from the drop-oldest one, and not counted as a drop.)
    src = _CountingSource(12, delay=0.001)  # drop_oldest_when_full defaults False
    slow = _Collector(handle_delay=0.01)
    src.connect(slow)
    pipe = Pipeline([src, slow])
    pipe.start()
    _run_until_done(pipe)

    assert src.stats._drops == 0
    assert slow.collected == sorted(slow.collected)
    assert slow.collected[0] == 0
    assert len(slow.collected) >= 11


def test_connecting_to_a_source_raises():
    src = _CountingSource(1)
    other = _CountingSource(1)
    with pytest.raises(ValueError, match="no input queue"):
        src.connect(other)


def test_sourcestage_process_item_is_not_implemented():
    with pytest.raises(NotImplementedError):
        _CountingSource(1).process_item(0)


def test_stop_is_safe_to_call_twice():
    src, out = _CountingSource(5), _Collector()
    src.connect(out)
    pipe = Pipeline([src, out])
    pipe.start()
    _run_until_done(pipe)
    pipe.stop(timeout=2.0)  # second call must not raise


def test_frame_context_defaults():
    ctx = FrameContext(frame_bgr=np.zeros((2, 2, 3), dtype=np.uint8), timestamp_ms=123)
    assert ctx.source_id == "default"
    assert ctx.face_found is False
    assert ctx.features == {}
    assert ctx.detection is None
    assert ctx.created_at > 0
    assert ctx.enqueued_at is None


def test_no_stage_threads_survive_shutdown():
    src, dbl, out = _CountingSource(10), _Doubler(), _Collector()
    src.connect(dbl).connect(out)
    pipe = Pipeline([src, dbl, out])
    pipe.start()
    _run_until_done(pipe)

    alive = [t.name for t in threading.enumerate()
             if t.name in {"counting_source", "doubler", "collector"}]
    assert alive == []
