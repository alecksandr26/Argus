"""PartsInferenceStage + the demo overlay, end to end on a synthetic frame with a stub detector
(no model files, no MediaPipe): crop -> detect -> remap -> track -> rules -> ctx -> overlay."""

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from cv_argus.model import parts_detector as pd_  # noqa: E402
from cv_argus.model.parts_geometry import square_face_box  # noqa: E402
from cv_argus.pipeline.parts_overlay import draw_parts_overlay  # noqa: E402
from cv_argus.pipeline.parts_stages import PartsInferenceStage  # noqa: E402
from cv_argus.pipeline.stage import FrameContext  # noqa: E402

FACE = (300, 160, 120, 150)                 # raw BlazeFace box (x, y, w, h) in a 640x480 frame
EYE_NORM = [0.30, 0.35, 0.42, 0.42]         # where the stub "sees" an eye, crop-normalised


class _StubDetector:
    precision = "int8"

    def __init__(self, cls):
        self.cls = cls
        self.last_phase_seconds = {"infer": 0.001, "decode": 0.0001}

    def detect(self, x):
        assert x.shape == (1, 160, 160, 1)
        return pd_.PartDetections(np.array([EYE_NORM], np.float32), np.array([0.9], np.float32),
                                  np.array([self.cls]))


def _ctx(ts_ms, face=True):
    ctx = FrameContext(frame_bgr=np.zeros((480, 640, 3), np.uint8), timestamp_ms=ts_ms)
    if face:
        ctx.face_found = True
        ctx.features["face_bbox_raw"] = FACE
        ctx.features["face_bbox"] = (270, 122, 450, 347)
    return ctx


def test_boxes_are_remapped_to_frame_pixels():
    stage = PartsInferenceStage(_StubDetector(pd_.OPEN_EYE))
    ctx = stage.process_item(_ctx(0))
    x0, y0, side = square_face_box(*FACE)
    [track] = ctx.features["parts"]["tracks"]
    expected = np.array(EYE_NORM) * side + np.array([x0, y0, x0, y0])
    np.testing.assert_allclose(track.box, expected, atol=1e-3)
    assert ctx.detection.class_name == "Not Drowsy" and ctx.detection.extras["severity"] == "none"


def test_closed_eyes_escalate_to_critical_drowsy():
    stage = PartsInferenceStage(_StubDetector(pd_.CLOSED_EYE))
    for k in range(17):                     # 3.2 s at 5 fps
        ctx = stage.process_item(_ctx(k * 200))
    assert ctx.detection.class_name == "Drowsy"
    assert ctx.detection.extras["severity"] == "critical" and ctx.detection.extras["reason"] == "eyes_closed"
    assert ctx.detection.probabilities[1] >= 0.57


def test_no_face_without_active_rule_gives_no_detection():
    stage = PartsInferenceStage(_StubDetector(pd_.OPEN_EYE))
    ctx = stage.process_item(_ctx(0, face=False))
    assert ctx.detection is None                       # orchestrator's "No Face"
    assert ctx.features["parts"]["tracks"] == []


def test_alarm_is_held_when_the_face_drops_out():
    stage = PartsInferenceStage(_StubDetector(pd_.CLOSED_EYE))
    for k in range(17):
        stage.process_item(_ctx(k * 200))
    ctx = stage.process_item(_ctx(17 * 200, face=False))
    assert ctx.detection is not None and ctx.detection.extras["severity"] == "critical"


def test_overlay_draws_without_error_and_changes_pixels():
    stage = PartsInferenceStage(_StubDetector(pd_.CLOSED_EYE))
    for k in range(12):
        ctx = stage.process_item(_ctx(k * 200))
    frame = ctx.frame_bgr.copy()
    draw_parts_overlay(frame, ctx, fps=5.0, camera_label="0")
    assert frame.any()
    x0, y0, side = square_face_box(*FACE)
    ex0, ey0 = int(x0 + EYE_NORM[0] * side), int(y0 + EYE_NORM[1] * side)
    assert tuple(frame[ey0, ex0]) == (0, 0, 255)       # closed eye box drawn red, on the eye
