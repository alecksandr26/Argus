"""parts_detector: decode/NMS (ported from notebook 12) and the backend-agnostic wrapper.
Real model files are never loaded here (hermetic suite); the backend is a stub."""

import numpy as np
import pytest

from cv_argus.model import parts_detector as pd_

G, C = 20, 5


def _logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def ideal_grid(boxes, classes):
    """The raw grid a perfect network would emit for these crop-normalised xyxy boxes (inverse
    of the decode formulas) -- the notebook's "perfect-net mAP 0.99" check."""
    raw = np.full((G, G, 5 + C), -8.0, np.float32)
    for (x0, y0, x1, y1), c in zip(boxes, classes):
        cx, cy, w, h = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0
        j, i = min(int(cx * G), G - 1), min(int(cy * G), G - 1)
        raw[i, j, 0] = 8.0
        raw[i, j, 1] = _logit((cx * G - j + 0.5) / 2)
        raw[i, j, 2] = _logit((cy * G - i + 0.5) / 2)
        raw[i, j, 3], raw[i, j, 4] = _logit(w), _logit(h)
        raw[i, j, 5 + c] = 8.0
    return raw


def test_decode_recovers_ideal_boxes():
    gt = np.array([[0.30, 0.35, 0.42, 0.42], [0.58, 0.35, 0.70, 0.42], [0.40, 0.65, 0.60, 0.85]], np.float32)
    cls = [pd_.OPEN_EYE, pd_.CLOSED_EYE, pd_.YAWN]
    d = pd_.decode_grid(ideal_grid(gt, cls))
    assert len(d) == 3
    order = np.argsort(d.boxes[:, 0] + d.boxes[:, 1])
    np.testing.assert_allclose(d.boxes[np.argsort(order)][np.argsort(np.argsort(order))], d.boxes)
    for b, c in zip(gt, cls):
        k = int(np.argmax(pd_.iou_xyxy(b[None], d.boxes)[0]))
        assert pd_.iou_xyxy(b[None], d.boxes[k:k + 1])[0, 0] > 0.99 and d.classes[k] == c


def test_decode_drops_low_scores_and_nms_merges_duplicates():
    raw = ideal_grid([[0.3, 0.3, 0.4, 0.4]], [pd_.OPEN_EYE])
    raw[6, 7] = raw[6, 6]                     # neighbour cell predicting the same box
    d = pd_.decode_grid(raw)
    assert len(d) == 1
    raw[..., 0] = -8.0                        # nothing confident
    assert len(pd_.decode_grid(raw)) == 0


def test_detector_wraps_any_backend_and_times_phases():
    gt = [[0.3, 0.3, 0.4, 0.4]]
    det = pd_.PartsDetector(lambda x: ideal_grid(gt, [pd_.HEAD_DOWN]), precision="int8")
    d = det.detect(np.zeros((1, 160, 160, 1), np.float32))
    assert d.classes.tolist() == [pd_.HEAD_DOWN]
    assert set(det.last_phase_seconds) == {"infer", "decode"}


def test_unknown_precision_is_rejected(monkeypatch):
    monkeypatch.setenv("PARTS_PRECISION", "fp16")
    with pytest.raises(SystemExit, match="PARTS_PRECISION"):
        pd_.PartsDetector.from_env()


def test_tflite_backend_matches_its_model(tmp_path):
    """The int8 path end to end on a tiny real model: same output shape the stage expects."""
    tf = pytest.importorskip("tensorflow")
    inp = tf.keras.Input((160, 160, 1))
    x = tf.keras.layers.Conv2D(10, 8, strides=8)(inp)
    m = tf.keras.Model(inp, x)
    # Same export path as notebook 12 (SavedModel -> converter). TF 2.16's from_keras_model
    # aborts the interpreter process under Keras 3, so don't use it here.
    m.export(str(tmp_path / "sm"), verbose=False)
    path = tmp_path / "m.tflite"
    path.write_bytes(tf.lite.TFLiteConverter.from_saved_model(str(tmp_path / "sm")).convert())
    out = pd_._TFLiteBackend(path, threads=1)(np.zeros((1, 160, 160, 1), np.float32))
    assert out.shape == (20, 20, 10)


def _pair_cell(cls_a, cls_b, p_a, p_b):
    raw = ideal_grid([[0.3, 0.3, 0.4, 0.4]], [cls_a])
    raw[7, 7, 5 + cls_a], raw[7, 7, 5 + cls_b] = _logit(p_a), _logit(p_b)
    return raw


@pytest.mark.parametrize("thr,expected", [(0.5, pd_.OPEN_EYE), (0.40, pd_.CLOSED_EYE)])
def test_closed_eye_threshold_moves_the_open_closed_decision(thr, expected):
    raw = _pair_cell(pd_.OPEN_EYE, pd_.CLOSED_EYE, 0.55, 0.45)   # ratio 0.45: argmax says open
    assert pd_.decode_grid(raw, closed_eye_thr=thr).classes.tolist() == [expected]


@pytest.mark.parametrize("thr,expected", [(0.5, pd_.HEAD_STRAIGHT), (0.30, pd_.HEAD_DOWN)])
def test_head_down_threshold_moves_the_straight_down_decision(thr, expected):
    raw = _pair_cell(pd_.HEAD_STRAIGHT, pd_.HEAD_DOWN, 0.65, 0.35)
    assert pd_.decode_grid(raw, head_down_thr=thr).classes.tolist() == [expected]


def test_pair_thresholds_leave_other_classes_alone():
    raw = ideal_grid([[0.4, 0.6, 0.6, 0.8]], [pd_.YAWN])
    assert pd_.decode_grid(raw, closed_eye_thr=0.0, head_down_thr=0.0).classes.tolist() == [pd_.YAWN]


def test_detector_thresholds_default_from_constants_and_env(monkeypatch):
    det = pd_.PartsDetector(lambda x: None, precision="int8")
    assert det.closed_eye_threshold == pd_.constants.PARTS_CLOSED_EYE_THRESHOLD
    assert det.head_down_threshold == pd_.constants.PARTS_HEAD_DOWN_THRESHOLD
    monkeypatch.setenv("PARTS_HEAD_DOWN_THRESHOLD", "0.2")
    monkeypatch.setenv("PARTS_CLOSED_EYE_THRESHOLD", "")
    assert pd_.env_float("PARTS_HEAD_DOWN_THRESHOLD") == 0.2
    assert pd_.env_float("PARTS_CLOSED_EYE_THRESHOLD") == pd_.constants.PARTS_CLOSED_EYE_THRESHOLD


# --- eye size/position filter (PARTS_EYE_MAX_FRAC / PARTS_EYE_CY_RANGE) --------------------------

def test_eye_filter_constants_match_the_contract():
    assert pd_.constants.PARTS_EYE_MAX_FRAC == 0.30
    assert pd_.constants.PARTS_EYE_CY_RANGE == (0.15, 0.70)


@pytest.mark.parametrize("cls", [pd_.OPEN_EYE, pd_.CLOSED_EYE])
def test_head_sized_eye_box_is_dropped(cls):
    raw = ideal_grid([[0.20, 0.20, 0.80, 0.70]], [cls])        # 0.6 x 0.5 "eye", centre y 0.45
    assert len(pd_.decode_grid(raw)) == 0
    assert pd_.decode_grid(raw, eye_max_frac=None).classes.tolist() == [cls]


def test_eye_size_limit_uses_the_larger_side():
    tall = ideal_grid([[0.40, 0.25, 0.48, 0.60]], [pd_.OPEN_EYE])     # 0.08 wide, 0.35 tall
    assert len(pd_.decode_grid(tall)) == 0
    ok = ideal_grid([[0.40, 0.30, 0.68, 0.50]], [pd_.OPEN_EYE])       # 0.28 x 0.20
    assert pd_.decode_grid(ok).classes.tolist() == [pd_.OPEN_EYE]


@pytest.mark.parametrize("cy,kept", [(0.10, False), (0.20, True), (0.65, True), (0.75, False)])
def test_eye_centre_must_be_in_the_upper_middle_band(cy, kept):
    raw = ideal_grid([[0.40, cy - 0.03, 0.52, cy + 0.03]], [pd_.CLOSED_EYE])
    assert len(pd_.decode_grid(raw)) == (1 if kept else 0)
    assert len(pd_.decode_grid(raw, eye_cy_range=None)) == 1


def test_eye_filter_leaves_mouth_and_head_alone():
    gt = [[0.05, 0.05, 0.95, 0.95], [0.35, 0.72, 0.65, 0.92]]        # head (big), yawn (low)
    d = pd_.decode_grid(ideal_grid(gt, [pd_.HEAD_STRAIGHT, pd_.YAWN]))
    assert sorted(d.classes.tolist()) == [pd_.YAWN, pd_.HEAD_STRAIGHT]


def test_eye_filter_runs_before_nms():
    """A slightly-too-big eye that scores higher would otherwise suppress the real eye under it
    (IoU ~0.63 >= 0.5); filtered first, the real eye survives."""
    good, bad = [0.31, 0.26, 0.59, 0.54], [0.34, 0.24, 0.66, 0.56]   # 0.28 vs 0.32 side
    raw = ideal_grid([good, bad], [pd_.OPEN_EYE, pd_.OPEN_EYE])
    raw[8, 9, 0] = 3.0                                                # the real eye scores lower
    assert pd_.iou_xyxy(np.array([good]), np.array([bad]))[0, 0] >= 0.5
    unfiltered = pd_.decode_grid(raw, eye_max_frac=None)
    assert len(unfiltered) == 1 and pd_.iou_xyxy(np.array([bad]), unfiltered.boxes)[0, 0] > 0.99
    filtered = pd_.decode_grid(raw)
    assert len(filtered) == 1 and pd_.iou_xyxy(np.array([good]), filtered.boxes)[0, 0] > 0.99


def test_detector_applies_the_eye_filter():
    big_eye = [[0.20, 0.20, 0.80, 0.70]]
    det = pd_.PartsDetector(lambda x: ideal_grid(big_eye, [pd_.CLOSED_EYE]), precision="int8")
    assert det.eye_max_frac == 0.30 and det.eye_cy_range == (0.15, 0.70)
    assert len(det.detect(np.zeros((1, 160, 160, 1), np.float32))) == 0
    off = pd_.PartsDetector(lambda x: ideal_grid(big_eye, [pd_.CLOSED_EYE]), precision="int8",
                            eye_max_frac=None, eye_cy_range=None)
    assert len(off.detect(np.zeros((1, 160, 160, 1), np.float32))) == 1
