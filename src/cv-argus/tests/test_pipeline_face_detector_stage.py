"""`pipeline/face_detector_stage.py` — only the pure `_expand_and_clip_bbox` helper is unit
tested here (it is a verbatim port of `notebook/06`'s crop-box maths). Constructing
`FaceDetectorCropStage` itself needs a real BlazeFace `.tflite` bundle and is covered by the
opt-in Docker tier / manual runs.
"""

from cv_argus.pipeline.face_detector_stage import _expand_and_clip_bbox


def test_zero_margin_is_the_raw_box_clipped():
    assert _expand_and_clip_bbox(50, 60, 100, 120, 1000, 1000, 0.0) == (50, 60, 150, 180)


def test_quarter_margin_expands_each_side():
    # w=h=100, margin 0.25 -> 25px each side
    assert _expand_and_clip_bbox(200, 200, 100, 100, 1000, 1000, 0.25) == (175, 175, 325, 325)


def test_expansion_clips_to_frame_bounds():
    # box hugging the top-left, big margin -> x0/y0 clamp to 0
    x0, y0, x1, y1 = _expand_and_clip_bbox(0, 0, 100, 100, 120, 120, 0.5)
    assert (x0, y0) == (0, 0)
    assert (x1, y1) == (120, 120)  # x+w+mx = 150 -> clipped to frame width


def test_expansion_clips_at_bottom_right():
    x0, y0, x1, y1 = _expand_and_clip_bbox(90, 90, 20, 20, 100, 100, 1.0)
    assert x1 == 100 and y1 == 100
    assert x0 == 70 and y0 == 70  # 90 - int(20*1.0)
