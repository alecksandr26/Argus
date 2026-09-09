"""`pipeline/mjpeg_output_stage.py` — the demo-only annotated MJPEG sink.

Only the pieces that don't bind a socket: `draw_detection_overlay()` (the standalone overlay
function, kept standalone precisely so it's reusable/testable without the HTTP machinery) and
`_FrameBroadcaster` (the single-slot latest-frame handoff).
"""

import threading
import time

import numpy as np

from cv_argus.model.detector import DetectionResult
from cv_argus.pipeline import FrameContext
from cv_argus.pipeline.mjpeg_output_stage import (
    _DEFAULT_COLOR,
    _STATUS_COLORS,
    _FrameBroadcaster,
    draw_detection_overlay,
)


def _ctx(detection=None, bbox=None):
    features = {}
    if bbox is not None:
        features["face_bbox"] = bbox
    return FrameContext(
        frame_bgr=np.zeros((240, 320, 3), dtype=np.uint8),
        timestamp_ms=0,
        detection=detection,
        features=features,
    )


class TestOverlay:
    def test_status_color_map_is_the_binary_scheme(self):
        assert _STATUS_COLORS["Not Drowsy"] == (0, 200, 0)      # green (BGR)
        assert _STATUS_COLORS["Drowsy"] == (0, 0, 255)          # red (BGR)
        assert _DEFAULT_COLOR == (255, 255, 255)

    def test_overlay_mutates_frame_in_place(self):
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        det = DetectionResult(level=1, probabilities=np.array([0.9, 0.1]))
        ret = draw_detection_overlay(frame, _ctx(detection=det, bbox=(10, 10, 100, 100)), fps=12.3)
        assert ret is None
        assert frame.any()  # text + box were drawn onto the same array

    def test_overlay_without_detection_does_not_raise(self):
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        draw_detection_overlay(frame, _ctx(detection=None), fps=0.0)  # label "No detection"

    def test_box_only_drawn_when_bbox_present(self):
        det = DetectionResult(level=2, probabilities=np.array([0.1, 0.9]))
        with_box = np.zeros((240, 320, 3), dtype=np.uint8)
        without_box = np.zeros((240, 320, 3), dtype=np.uint8)
        draw_detection_overlay(with_box, _ctx(detection=det, bbox=(20, 20, 200, 200)), fps=10.0)
        draw_detection_overlay(without_box, _ctx(detection=det, bbox=None), fps=10.0)
        # the rectangle adds coloured pixels far below the top-left status text
        assert with_box[150, 20].any()
        assert not without_box[150, 20].any()


class TestFrameBroadcaster:
    def test_wait_times_out_when_nothing_published(self):
        b = _FrameBroadcaster()
        assert b.wait_for_next(last_seen_version=0, timeout=0.05) is None

    def test_publish_then_wait_returns_frame_and_version(self):
        b = _FrameBroadcaster()
        b.publish(b"jpeg-1")
        assert b.wait_for_next(last_seen_version=0, timeout=1.0) == (b"jpeg-1", 1)

    def test_version_increases_per_publish(self):
        b = _FrameBroadcaster()
        b.publish(b"a")
        b.publish(b"b")
        frame, version = b.wait_for_next(last_seen_version=1, timeout=1.0)
        assert frame == b"b"
        assert version == 2

    def test_waiter_is_woken_by_a_later_publish(self):
        b = _FrameBroadcaster()
        got = []

        def _waiter():
            got.append(b.wait_for_next(last_seen_version=0, timeout=2.0))

        t = threading.Thread(target=_waiter)
        t.start()
        time.sleep(0.05)
        b.publish(b"late")
        t.join(timeout=2.0)
        assert got == [(b"late", 1)]
