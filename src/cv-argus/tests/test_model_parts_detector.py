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
