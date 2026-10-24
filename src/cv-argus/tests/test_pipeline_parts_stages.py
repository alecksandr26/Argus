"""PartsInferenceStage + the demo overlay, end to end on a synthetic frame with a stub detector
(no model files, no MediaPipe): crop -> detect -> remap -> track -> rules -> ctx -> overlay."""

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from cv_argus.model import parts_detector as pd_  # noqa: E402
from cv_argus.model.parts_geometry import square_face_box  # noqa: E402
from cv_argus.orchestrator.parts_rules import PartsRuleEngine  # noqa: E402
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


# --- face switch (contracts C1.7) ---------------------------------------------------------------

class _RecordingRules(PartsRuleEngine):
    """The real engine, recording the order of `update` / `reset_signals` calls: the stage must
    call `reset_signals` exactly once per switch, after the switch frame went through `update`."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def update(self, t, s):
        self.calls.append(("update", s.face))
        return super().update(t, s)

    def reset_signals(self):
        self.calls.append(("reset", None))
        super().reset_signals()


def _switch_ctx(ts_ms):
    ctx = _ctx(ts_ms, face=False)
    ctx.features["face_switched"] = True
    return ctx


def test_face_switch_resets_rule_timers_and_tracker_after_the_rules_see_the_frame():
    rules = _RecordingRules()
    stage = PartsInferenceStage(_StubDetector(pd_.OPEN_EYE), rules=rules)
    for k in range(3):
        ctx = stage.process_item(_ctx(k * 200))
    assert ctx.features["parts"]["tracks"]
    ctx = stage.process_item(_switch_ctx(600))
    assert rules.calls[-2:] == [("update", False), ("reset", None)]
    assert [c for c, _ in rules.calls].count("reset") == 1
    assert ctx.features["parts"]["tracks"] == [] and stage._tracker.tracks == []
    ctx = stage.process_item(_ctx(800))                   # the new face: fresh track IDs
    assert [t.id for t in ctx.features["parts"]["tracks"]] == ["E1"]
    assert [c for c, _ in rules.calls].count("reset") == 1


def test_face_switch_restarts_the_eye_timer_for_the_new_face():
    # 1.4 s of closed eyes reaches low; the switch frame, then the new face with closed eyes.
    # Without the reset the eye timer would carry on past RULE_EYES_MEDIUM_S (2.0 s) by 2.6 s;
    # with it the new face's closure started at 1.8 s, so 0.8 s in it is still the held low.
    stage = PartsInferenceStage(_StubDetector(pd_.CLOSED_EYE))
    for k in range(8):                                    # 0 .. 1.4 s closed
        ctx = stage.process_item(_ctx(k * 200))
    assert ctx.detection.extras["severity"] == "low"
    stage.process_item(_switch_ctx(1600))
    for ts in range(1800, 2800, 200):                     # 1.8 .. 2.6 s, the new face closed
        ctx = stage.process_item(_ctx(ts))
    assert ctx.detection.extras["severity"] == "low"


def test_face_switch_needs_a_rule_engine_with_reset_signals():
    class _NoReset:                                       # an engine predating reset_signals
        def __init__(self):
            self._inner = PartsRuleEngine()

        def update(self, t, s):
            return self._inner.update(t, s)

    stage = PartsInferenceStage(_StubDetector(pd_.OPEN_EYE), rules=_NoReset())
    stage.process_item(_ctx(0))
    with pytest.raises(AttributeError):
        stage.process_item(_switch_ctx(200))


def test_face_switch_keeps_the_held_severity():
    stage = PartsInferenceStage(_StubDetector(pd_.CLOSED_EYE))
    for k in range(17):                                   # 3.2 s closed -> critical
        stage.process_item(_ctx(k * 200))
    ctx = stage.process_item(_switch_ctx(17 * 200))
    assert ctx.detection.extras["severity"] == "critical"
    stage._detector = _StubDetector(pd_.OPEN_EYE)         # the new face, eyes open
    ctx = stage.process_item(_ctx(18 * 200))
    assert ctx.detection.extras["severity"] == "critical"  # still needs RULE_RECOVERY_S to drop


def test_driver_leaves_and_a_passenger_stays_no_face_critical_holds_through_the_switch(monkeypatch):
    """The whole chain (fake BlazeFace -> FaceDetectorCropStage -> PartsInferenceStage): the
    no-face CRITICAL fires on or before the switch frame and stays held until the new face has
    been clear for RULE_RECOVERY_S (contracts C1.7 [sync 1])."""
    pytest.importorskip("mediapipe")
    from types import SimpleNamespace

    from cv_argus import constants
    from cv_argus.pipeline.face_detector_stage import FaceDetectorCropStage

    monkeypatch.setenv("RULE_NO_FACE_CRITICAL_S", "5.0")  # C4 value (T1 changes the default)
    driver, passenger = FACE, (40, 180, 80, 100)

    def det(box):
        x, y, w, h = box
        return SimpleNamespace(bounding_box=SimpleNamespace(origin_x=x, origin_y=y, width=w, height=h),
                               categories=[SimpleNamespace(score=0.9)])

    frames = [[det(driver), det(passenger)]] + [[det(passenger)]] * 45

    class _Fake:
        def detect_for_video(self, image, ts_ms):
            return SimpleNamespace(detections=frames[ts_ms // 200])

        def close(self):
            pass

    face = FaceDetectorCropStage(detector=_Fake())
    parts = PartsInferenceStage(_StubDetector(pd_.OPEN_EYE))
    out = [parts.process_item(face.process_item(_ctx(k * 200, face=False))) for k in range(len(frames))]

    switch = [k for k, c in enumerate(out) if c.features.get("face_switched")]
    assert len(switch) == 1
    s = switch[0]
    first_critical = next(k for k, c in enumerate(out)
                          if c.detection is not None and c.detection.extras["severity"] == "critical")
    assert first_critical <= s
    assert out[first_critical].detection.extras["reason"] == "no_face"
    recovery_frames = int(round(constants.RULE_RECOVERY_S / 0.2))
    for c in out[s:s + 1 + recovery_frames]:              # held through the switch and the recovery wait
        assert c.detection.extras["severity"] == "critical"
    assert out[s + 1].face_found and out[s + 1].features["face_bbox_raw"] == passenger
    assert out[-1].detection.extras["severity"] == "none"  # the new face recovered


# --- face lock end to end (contracts C1.7 [sync 2]) ---------------------------------------------

PASSENGER = (40, 180, 80, 100)              # side 100; the driver (FACE) is 1.5x that


def _fake_blazeface(frames):
    from types import SimpleNamespace

    def det(box):
        x, y, w, h = box
        return SimpleNamespace(bounding_box=SimpleNamespace(origin_x=x, origin_y=y, width=w, height=h),
                               categories=[SimpleNamespace(score=0.9)])

    class _Fake:
        def detect_for_video(self, image, ts_ms):
            return SimpleNamespace(detections=[det(b) for b in frames[ts_ms // 200]])

        def close(self):
            pass

    return _Fake()


class _WhoseEyesDetector(_StubDetector):
    """Eyes closed when the crop holds the passenger (the bright patch painted on the frame),
    open for the driver's (dark) crop: the rules' verdict shows whose face the lock is on."""

    def __init__(self):
        super().__init__(pd_.OPEN_EYE)

    def detect(self, x):
        self.cls = pd_.CLOSED_EYE if x.max() > 0.5 else pd_.OPEN_EYE
        return super().detect(x)


def _run_chain(frames, detector, rules=None):
    pytest.importorskip("mediapipe")
    from cv_argus.pipeline.face_detector_stage import FaceDetectorCropStage

    face = FaceDetectorCropStage(detector=_fake_blazeface(frames))
    parts = PartsInferenceStage(detector, rules=rules)
    out = []
    px, py, pw, ph = PASSENGER
    for k in range(len(frames)):
        ctx = _ctx(k * 200, face=False)
        ctx.frame_bgr[py:py + ph, px:px + pw] = 255
        out.append(parts.process_item(face.process_item(ctx)))
    return out


def _severity(ctx):
    return "none" if ctx.detection is None else ctx.detection.extras["severity"]


def test_lone_driver_whose_box_jumps_100px_gets_no_no_face_critical(monkeypatch):
    """Reviewer repro: the driver's box (120x150) moves 100 px between two frames with nobody
    else in view. Round 1 read that as no face and fired a no_face CRITICAL 5 s later."""
    monkeypatch.setenv("RULE_NO_FACE_CRITICAL_S", "5.0")
    jumped = (FACE[0] + 100, FACE[1], FACE[2], FACE[3])
    frames = [[FACE]] * 10 + [[jumped]] * 40
    out = _run_chain(frames, _StubDetector(pd_.OPEN_EYE))
    assert all(c.face_found for c in out)
    assert all(_severity(c) == "none" for c in out)
    assert not any(c.features.get("face_switched") for c in out)
    assert out[-1].features["face_bbox_raw"] == jumped


@pytest.mark.parametrize("driver", [FACE, (300, 160, 104, 130)], ids=["1.5x", "1.3x"])
def test_driver_returning_while_the_passenger_stays_is_relocked_after_5s_held_severity_kept(
        monkeypatch, driver):
    """Reviewer repro: the driver steps out, the passenger stays, the lock goes to the passenger
    at 5 s. The driver comes back and takes the lock back 5 s later (take-over; run with a driver
    1.5x and 1.3x the passenger's side, the latter being C1.7's literal required case). The
    passenger's closed eyes left a CRITICAL held; it survives the take-over and only drops after
    RULE_RECOVERY_S of the driver's open eyes."""
    from cv_argus import constants

    monkeypatch.setenv("RULE_NO_FACE_CRITICAL_S", "5.0")
    n_switch = int(constants.FACE_SWITCH_AFTER_S / 0.2)            # 25 frames
    frames = ([[driver, PASSENGER]] + [[PASSENGER]] * (n_switch + 1)  # switch on frame 26
              + [[PASSENGER, driver]] * (n_switch + 30))
    rules = _RecordingRules()
    out = _run_chain(frames, _WhoseEyesDetector(), rules=rules)

    switches = [k for k, c in enumerate(out) if c.features.get("face_switched")]
    assert len(switches) == 2
    s1, s2 = switches
    assert s1 == n_switch + 1
    assert out[s1 + 1].features["face_bbox_raw"] == PASSENGER
    assert s2 == s1 + 1 + n_switch                                    # driver back on s1+1, 5 s later
    assert out[s2].features["face_bbox_raw"] == PASSENGER            # take-over frame: old face
    assert out[s2].features["face_lock"]["takeover_s"] == pytest.approx(constants.FACE_SWITCH_AFTER_S)
    assert out[s2 + 1].features["face_bbox_raw"] == driver           # driver re-locked
    assert [c for c, _ in rules.calls].count("reset") == 2
    assert _severity(out[s2 - 1]) == "critical"                      # the passenger's eyes
    recovery = int(round(constants.RULE_RECOVERY_S / 0.2))
    for c in out[s2:s2 + recovery]:                                  # held through the take-over
        assert _severity(c) == "critical"
    assert _severity(out[-1]) == "none"                              # the driver recovered it
