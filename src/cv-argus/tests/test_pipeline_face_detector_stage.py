"""`pipeline/face_detector_stage.py`: the `_expand_and_clip_bbox` helper (a verbatim port of
`notebook/06`'s crop-box maths), the pure `FaceLock` (largest face, sticky, timed switch, contracts
C1.7), and `FaceDetectorCropStage` end to end with a fake MediaPipe detector (no `.tflite`
bundle needed).
"""

from types import SimpleNamespace

import numpy as np
import pytest

from cv_argus import constants
from cv_argus.pipeline.face_detector_stage import FaceLock, _expand_and_clip_bbox


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


# --- FaceLock -----------------------------------------------------------------------------------

DRIVER = (300, 160, 120, 150)       # big, near the camera
PASSENGER = (40, 180, 80, 100)      # smaller, to the side
FPS_DT = 0.2                        # SAMPLE_FPS = 5


def test_switch_constant_matches_the_contract():
    assert constants.FACE_SWITCH_AFTER_S == 5.0


def test_no_face_and_no_lock_reads_missing():
    lock = FaceLock()
    r = lock.update(0.0, [])
    assert r.box is None and r.faces == 0 and not r.switched and lock.locked_box is None


def test_locks_the_largest_face():
    r = FaceLock().update(0.0, [PASSENGER, DRIVER])
    assert r.box == DRIVER and r.faces == 2 and r.ignored == 1


def test_lock_is_sticky_against_a_bigger_face_appearing():
    lock = FaceLock()
    lock.update(0.0, [PASSENGER])                       # only the passenger: it's locked
    bigger = (300, 100, 200, 220)
    r = lock.update(0.2, [PASSENGER, bigger])
    assert r.box == PASSENGER and r.ignored == 1 and r.switch_elapsed_s is None


def test_lock_follows_the_locked_face_as_it_moves():
    lock = FaceLock()
    lock.update(0.0, [DRIVER])
    box = DRIVER
    for k in range(1, 20):                              # drifts 20 px/frame, far over 20 frames
        box = (box[0] + 20, box[1], box[2], box[3])
        r = lock.update(k * FPS_DT, [PASSENGER, box])
        assert r.box == box
    assert lock.locked_box[0] == DRIVER[0] + 19 * 20


def test_far_or_differently_sized_face_is_not_the_locked_one():
    lock = FaceLock()
    lock.update(0.0, [DRIVER])
    far = (DRIVER[0] + 200, DRIVER[1], DRIVER[2], DRIVER[3])    # > 0.6 * 150 px away
    tiny = (DRIVER[0], DRIVER[1], 40, 50)                       # same place, a third of the size
    assert lock.update(0.2, [far]).box is None
    assert lock.update(0.4, [tiny]).box is None


def test_locked_face_missing_with_another_face_reads_missing_until_the_switch():
    lock = FaceLock()
    lock.update(0.0, [DRIVER, PASSENGER])
    t, results = 0.0, []
    while True:                                          # driver leaves; passenger stays
        t = round(t + FPS_DT, 6)
        r = lock.update(t, [PASSENGER])
        results.append((t, r))
        if r.switched:
            break
    t_start = results[0][0]
    t_switch, r_switch = results[-1]
    assert all(r.box is None for _, r in results)       # switch frame included: still missing
    assert t_switch - t_start == pytest.approx(constants.FACE_SWITCH_AFTER_S)
    assert r_switch.switch_elapsed_s >= constants.FACE_SWITCH_AFTER_S
    assert [r.switched for _, r in results].count(True) == 1
    assert lock.locked_box == PASSENGER
    nxt = lock.update(t_switch + FPS_DT, [PASSENGER])   # the new face is used from the next frame
    assert nxt.box == PASSENGER and not nxt.switched


def test_switch_goes_to_the_largest_visible_face():
    lock = FaceLock(switch_after_s=1.0)
    lock.update(0.0, [DRIVER])
    other_big = (450, 150, 110, 140)
    for k in range(1, 7):
        r = lock.update(k * FPS_DT, [PASSENGER, other_big])
    assert r.switched and lock.locked_box == other_big


def test_switch_timer_resets_when_the_locked_face_returns():
    lock = FaceLock(switch_after_s=1.0)
    lock.update(0.0, [DRIVER])
    for k in range(1, 5):                               # 0.8 s gone, passenger in view
        assert not lock.update(k * FPS_DT, [PASSENGER]).switched
    assert lock.update(1.0, [DRIVER, PASSENGER]).box == DRIVER
    for k in range(6, 10):                              # gone again: the timer restarts at 1.2
        r = lock.update(k * FPS_DT, [PASSENGER])
        assert not r.switched
    assert r.switch_elapsed_s == pytest.approx(0.6)
    assert lock.locked_box == DRIVER


def test_switch_timer_resets_when_no_other_face_is_visible():
    lock = FaceLock(switch_after_s=1.0)
    lock.update(0.0, [DRIVER])
    for k in range(1, 5):
        lock.update(k * FPS_DT, [PASSENGER])
    r = lock.update(1.0, [])                            # nobody in view for one frame
    assert r.box is None and r.switch_elapsed_s is None
    for k in range(6, 10):
        r = lock.update(k * FPS_DT, [PASSENGER])
        assert not r.switched
    assert lock.locked_box == DRIVER


def test_locked_face_returning_after_a_gap_is_locked_again():
    lock = FaceLock()
    lock.update(0.0, [DRIVER])
    for k in range(1, 50):                              # 10 s with nobody in view
        assert lock.update(k * FPS_DT, []).box is None
    back = (DRIVER[0] + 30, DRIVER[1] + 10, DRIVER[2] + 10, DRIVER[3])
    assert lock.update(10.0, [PASSENGER, back]).box == back


def test_reset_drops_the_lock():
    lock = FaceLock()
    lock.update(0.0, [PASSENGER])
    lock.reset()
    assert lock.update(0.2, [PASSENGER, DRIVER]).box == DRIVER


# --- FaceDetectorCropStage with a fake MediaPipe detector ----------------------------------------

def _det(box, score=0.9):
    x, y, w, h = box
    return SimpleNamespace(bounding_box=SimpleNamespace(origin_x=x, origin_y=y, width=w, height=h),
                           categories=[SimpleNamespace(score=score)])


class _FakeDetector:
    def __init__(self, frames):
        self.frames = list(frames)       # one list of detections per call
        self.closed = False

    def detect_for_video(self, image, ts_ms):
        return SimpleNamespace(detections=self.frames.pop(0))

    def close(self):
        self.closed = True


def _stage(frames, **kw):
    pytest.importorskip("cv2")
    pytest.importorskip("mediapipe")
    from cv_argus.pipeline.face_detector_stage import FaceDetectorCropStage
    return FaceDetectorCropStage(detector=_FakeDetector(frames), **kw)


def _ctx(ts_ms):
    from cv_argus.pipeline.stage import FrameContext
    return FrameContext(frame_bgr=np.zeros((480, 640, 3), np.uint8), timestamp_ms=ts_ms)


def test_stage_picks_the_largest_face_not_the_most_confident():
    stage = _stage([[_det(PASSENGER, 0.99), _det(DRIVER, 0.6)]])
    ctx = stage.process_item(_ctx(0))
    assert ctx.face_found and ctx.features["face_bbox_raw"] == DRIVER
    assert ctx.features["face_bbox"] == _expand_and_clip_bbox(*DRIVER, 640, 480, 0.25)
    assert ctx.features["face_lock"]["faces"] == 2 and ctx.features["face_lock"]["ignored"] == 1
    assert "face_switched" not in ctx.features
    assert "face" in ctx.features["timing_ms"]


def test_stage_reports_no_face_and_flags_the_switch_frame():
    n_before = int(constants.FACE_SWITCH_AFTER_S / FPS_DT)            # 25 frames of passenger only
    frames = [[_det(DRIVER), _det(PASSENGER)]] + [[_det(PASSENGER)]] * (n_before + 2)
    stage = _stage(frames)
    ctxs = [stage.process_item(_ctx(k * 200)) for k in range(len(frames))]
    assert ctxs[0].face_found
    gone = ctxs[1:n_before + 2]
    assert not any(c.face_found for c in gone)
    flagged = [k for k, c in enumerate(ctxs) if c.features.get("face_switched")]
    assert flagged == [n_before + 1]                                  # 1.0 s + 5 s -> frame 26
    assert ctxs[-1].face_found and ctxs[-1].features["face_bbox_raw"] == PASSENGER


def test_stage_skips_degenerate_boxes():
    stage = _stage([[_det((700, 500, 30, 30))]])                       # entirely off a 640x480 frame
    ctx = stage.process_item(_ctx(0))
    assert not ctx.face_found and ctx.features["face_lock"]["faces"] == 0


def test_stage_close_closes_the_detector():
    stage = _stage([])
    stage.close()
    assert stage._detector.closed
