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

DRIVER = (300, 160, 120, 150)       # big, near the camera (side 150)
PASSENGER = (40, 180, 80, 100)      # smaller, to the side (side 100)
FPS_DT = 0.2                        # SAMPLE_FPS = 5


def _ts(k):
    return round(k * FPS_DT, 6)


def test_face_lock_constants_match_the_contract():
    assert constants.FACE_SWITCH_AFTER_S == 5.0
    assert constants.FACE_LOCK_MAX_SHIFT_FRAC == 0.6
    assert constants.FACE_LOCK_MAX_SCALE == 1.6
    assert constants.FACE_LOCK_MISS_GRACE_S == 1.0
    assert constants.FACE_LOCK_TAKEOVER_SCALE == 1.25


def test_no_face_and_no_lock_reads_missing():
    lock = FaceLock()
    r = lock.update(0.0, [])
    assert r.box is None and r.faces == 0 and not r.switched and lock.locked_box is None


def test_locks_the_largest_face_and_the_others_become_bystanders():
    lock = FaceLock()
    r = lock.update(0.0, [PASSENGER, DRIVER])
    assert r.box == DRIVER and r.faces == 2 and r.ignored == 1
    assert lock.bystanders == [PASSENGER]


def test_a_second_face_less_than_takeover_scale_larger_is_ignored_for_good():
    lock = FaceLock()
    lock.update(0.0, [PASSENGER])                       # only the passenger: it's locked
    slightly = (300, 160, 88, 110)                      # side 110 = 1.1x the passenger's
    for k in range(1, 100):                             # 20 s side by side
        r = lock.update(_ts(k), [PASSENGER, slightly])
        assert r.box == PASSENGER and r.ignored == 1 and not r.switched
        assert r.switch_elapsed_s is None and r.takeover_elapsed_s is None
    assert lock.locked_box == PASSENGER


def test_lock_follows_the_locked_face_as_it_moves():
    lock = FaceLock()
    lock.update(0.0, [DRIVER, PASSENGER])
    box = DRIVER
    for k in range(1, 20):                              # drifts 20 px/frame, far over 20 frames
        box = (box[0] + 20, box[1], box[2], box[3])
        r = lock.update(_ts(k), [PASSENGER, box])
        assert r.box == box
    assert lock.locked_box[0] == DRIVER[0] + 19 * 20


def test_far_or_differently_sized_face_next_to_the_locked_one_is_not_it():
    lock = FaceLock()
    lock.update(0.0, [DRIVER])
    far = (DRIVER[0] + 200, DRIVER[1], DRIVER[2], DRIVER[3])    # > 0.6 * 150 px away
    tiny = (DRIVER[0] + 5, DRIVER[1], 40, 50)                   # same place, a third of the size
    r = lock.update(0.2, [far, DRIVER])
    assert r.box == DRIVER and r.ignored == 1
    r = lock.update(0.4, [tiny, DRIVER])
    assert r.box == DRIVER and r.ignored == 1


# Reviewer repro 1 (C1.7 [sync 2] rule 1): a lone driver must never read as "no face" because
# their box jumped.

def test_lone_driver_whose_box_jumps_has_no_no_face_frame():
    lock = FaceLock()
    lock.update(0.0, [DRIVER])
    jumped = (DRIVER[0] + 100, DRIVER[1], DRIVER[2], DRIVER[3])  # 100 px > 0.6 * 150 = 90 px
    r = lock.update(0.2, [jumped])
    assert r.box == jumped and r.reacquired and not r.switched and r.switch_elapsed_s is None
    for k in range(2, 60):                               # and it stays locked afterwards
        r = lock.update(_ts(k), [jumped])
        assert r.box == jumped and not r.switched


def test_lone_driver_returning_displaced_after_a_2s_gap_is_missing_only_during_the_gap():
    lock = FaceLock()
    lock.update(0.0, [DRIVER])
    missing = []
    for k in range(1, 11):                               # 2 s with nobody in view
        r = lock.update(_ts(k), [])
        missing.append(r.box is None)
        assert r.switch_elapsed_s is None and not r.switched
    assert all(missing)
    back = (DRIVER[0] - 150, DRIVER[1] + 40, DRIVER[2] + 10, DRIVER[3] + 10)   # well displaced
    r = lock.update(_ts(11), [back])
    assert r.box == back and r.reacquired and not r.switched


def test_a_new_face_next_to_a_bystander_is_reacquired_not_switched_to():
    lock = FaceLock()
    lock.update(0.0, [DRIVER, PASSENGER])
    jumped = (DRIVER[0] + 100, DRIVER[1], DRIVER[2], DRIVER[3])
    r = lock.update(0.2, [PASSENGER, jumped])
    assert r.box == jumped and r.reacquired and r.ignored == 1
    assert lock.bystanders == [PASSENGER]


# Rule 2: driver leaves, the passenger (seen with the driver before) stays.

def _leave_and_switch(frames_after):
    """Driver + passenger on frame 0, then `frames_after(k)` for k = 1, 2, ...; returns the
    results until the switch frame, inclusive."""
    lock = FaceLock()
    lock.update(0.0, [DRIVER, PASSENGER])
    results = []
    for k in range(1, 200):
        r = lock.update(_ts(k), frames_after(k))
        results.append((_ts(k), r))
        if r.switched:
            return lock, results
    raise AssertionError("no switch")


def test_locked_face_missing_with_a_bystander_reads_missing_until_the_switch():
    lock, results = _leave_and_switch(lambda k: [PASSENGER])
    t_switch, r_switch = results[-1]
    assert all(r.box is None for _, r in results)       # switch frame included: still missing
    assert not any(r.reacquired for _, r in results)
    assert t_switch - results[0][0] == pytest.approx(constants.FACE_SWITCH_AFTER_S)
    assert r_switch.switch_elapsed_s >= constants.FACE_SWITCH_AFTER_S
    assert [r.switched for _, r in results].count(True) == 1
    assert lock.locked_box == PASSENGER
    nxt = lock.update(t_switch + FPS_DT, [PASSENGER])   # the new face is used from the next frame
    assert nxt.box == PASSENGER and not nxt.switched


def test_one_frame_passenger_misses_after_the_driver_left_keep_the_5s_switch():
    # The passenger is missed on every 4th frame (never on the 5.0 s frame).
    lock, results = _leave_and_switch(lambda k: [] if k % 4 == 3 else [PASSENGER])
    t_switch, _ = results[-1]
    assert all(r.box is None for _, r in results)
    assert t_switch - results[0][0] == pytest.approx(constants.FACE_SWITCH_AFTER_S)
    assert lock.locked_box == PASSENGER


def test_a_passenger_miss_on_the_frame_before_the_driver_leaves_doesnt_make_it_new():
    lock = FaceLock()
    lock.update(0.0, [DRIVER, PASSENGER])
    lock.update(0.2, [DRIVER])                           # detector misses the passenger once
    r = lock.update(0.4, [PASSENGER])                    # driver gone, passenger back
    assert r.box is None and not r.reacquired and r.switch_elapsed_s == 0.0


def test_a_passenger_shifting_while_the_driver_is_away_stays_a_bystander():
    def frames(k):
        x = PASSENGER[0] + 6 * k                          # 6 px/frame drift
        return [(x, PASSENGER[1], PASSENGER[2], PASSENGER[3])]

    lock, results = _leave_and_switch(frames)
    assert all(r.box is None for _, r in results)
    assert results[-1][0] - results[0][0] == pytest.approx(constants.FACE_SWITCH_AFTER_S)


def test_bystander_unseen_beyond_the_grace_is_dropped_and_then_reacquired():
    lock = FaceLock()
    lock.update(0.0, [DRIVER, PASSENGER])
    for k in range(1, 7):                                 # 1.2 s with nobody in view
        lock.update(_ts(k), [])
    assert lock.bystanders == []
    r = lock.update(_ts(7), [PASSENGER])                  # known limit: re-acquired at once
    assert r.box == PASSENGER and r.reacquired


def test_switch_goes_to_the_largest_visible_face():
    lock = FaceLock(switch_after_s=1.0)
    other_big = (450, 150, 110, 140)
    lock.update(0.0, [DRIVER, PASSENGER, other_big])
    for k in range(1, 7):
        r = lock.update(_ts(k), [PASSENGER, other_big])
    assert r.switched and lock.locked_box == other_big
    assert lock.bystanders == [PASSENGER]


def test_switch_timer_resets_when_the_locked_face_returns():
    lock = FaceLock(switch_after_s=1.0)
    lock.update(0.0, [DRIVER, PASSENGER])
    for k in range(1, 5):                               # 0.8 s gone, passenger in view
        assert not lock.update(_ts(k), [PASSENGER]).switched
    assert lock.update(1.0, [DRIVER, PASSENGER]).box == DRIVER
    for k in range(6, 10):                              # gone again: the timer restarts at 1.2
        r = lock.update(_ts(k), [PASSENGER])
        assert not r.switched
    assert r.switch_elapsed_s == pytest.approx(0.6)
    assert lock.locked_box == DRIVER


def test_switch_timer_resets_when_nothing_is_visible_beyond_the_grace():
    lock = FaceLock(switch_after_s=1.0)
    lock.update(0.0, [DRIVER, PASSENGER])
    for k in range(1, 5):
        lock.update(_ts(k), [PASSENGER])
    r = lock.update(1.0, [])                            # one empty frame: inside the grace
    assert r.box is None and r.switch_elapsed_s == pytest.approx(0.8)
    for k in range(6, 12):                              # 1.2 s empty: bystander dropped, reset
        r = lock.update(_ts(k), [])
    assert r.switch_elapsed_s is None and lock.bystanders == []
    assert lock.locked_box == DRIVER


def test_locked_face_returning_after_a_gap_is_locked_again():
    lock = FaceLock()
    lock.update(0.0, [DRIVER])
    for k in range(1, 50):                              # 10 s with nobody in view
        assert lock.update(_ts(k), []).box is None
    back = (DRIVER[0] + 30, DRIVER[1] + 10, DRIVER[2] + 10, DRIVER[3])
    assert lock.update(10.0, [PASSENGER, back]).box == back


# Take-over (reviewer repro 2): the driver comes back while the passenger holds the lock.

def test_driver_returning_larger_takes_the_lock_back_after_5s():
    lock, results = _leave_and_switch(lambda k: [PASSENGER])
    assert lock.locked_box == PASSENGER
    k0 = round(results[-1][0] / FPS_DT) + 1
    out = []
    for k in range(k0, k0 + 60):                          # the driver is back, 1.5x the side
        r = lock.update(_ts(k), [PASSENGER, DRIVER])
        out.append((_ts(k), r))
        if r.switched:
            break
    t_take, r_take = out[-1]
    assert r_take.switched and r_take.box == PASSENGER   # the take-over frame reads the old face
    assert t_take - out[0][0] == pytest.approx(constants.FACE_SWITCH_AFTER_S)
    assert all(r.box == PASSENGER and not r.switched for _, r in out[:-1])
    assert lock.locked_box == DRIVER and lock.bystanders == [PASSENGER]
    nxt = lock.update(t_take + FPS_DT, [PASSENGER, DRIVER])
    assert nxt.box == DRIVER and not nxt.switched and nxt.takeover_elapsed_s is None


def test_takeover_at_1_3x_also_works_when_the_passenger_was_alone_at_boot():
    lock = FaceLock()
    lock.update(0.0, [PASSENGER])
    driver = (300, 160, 104, 130)                         # side 130 = 1.3x the passenger's
    for k in range(1, 26):
        r = lock.update(_ts(k), [PASSENGER, driver])
        assert r.box == PASSENGER and not r.switched
    r = lock.update(_ts(26), [PASSENGER, driver])        # 5.0 s after it first appeared (0.2 s)
    assert r.switched and r.takeover_elapsed_s == pytest.approx(5.0)
    assert lock.locked_box == driver


def test_takeover_tolerates_one_frame_misses_of_the_larger_face():
    lock = FaceLock()
    lock.update(0.0, [PASSENGER])
    seen = []
    for k in range(1, 40):
        boxes = [PASSENGER] if k % 5 == 2 else [PASSENGER, DRIVER]
        r = lock.update(_ts(k), boxes)
        seen.append(r)
        if r.switched:
            break
    assert seen[-1].switched and seen[-1].takeover_elapsed_s == pytest.approx(5.0)


def test_takeover_timer_restarts_when_the_larger_face_is_gone_beyond_the_grace():
    lock = FaceLock()
    lock.update(0.0, [PASSENGER])
    for k in range(1, 15):                                # 2.8 s next to the passenger
        lock.update(_ts(k), [PASSENGER, DRIVER])
    for k in range(15, 22):                               # 1.4 s gone
        lock.update(_ts(k), [PASSENGER])
    r = lock.update(_ts(22), [PASSENGER, DRIVER])
    assert r.takeover_elapsed_s == 0.0 and not r.switched


def test_reset_drops_the_lock():
    lock = FaceLock()
    lock.update(0.0, [PASSENGER])
    lock.reset()
    assert lock.update(0.2, [PASSENGER, DRIVER]).box == DRIVER
    assert lock.bystanders == [PASSENGER]


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
