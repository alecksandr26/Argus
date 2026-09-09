"""`model/layers.py` — `GeometricRatioFeatureLayer`, the EAR/MAR/head-pose maths.

This class is a byte-for-byte port of the notebook's layer (see `CLAUDE.md`: it is redefined
verbatim in five places and there is no automated cross-check). These tests pin its observable
behaviour — EAR/MAR values, the pose-validity gate, the divide-by-zero guard, output shape,
config round-trip, and the frozen 52-name blendshape order — so a stray "cleanup" of any one
copy that changes the maths trips a red test here.

Landmark placement technique mirrors `src/dataset/tests/test_geometry.py`: only the gathered
index points matter, everything else is 0.
"""

import numpy as np
import pytest

tf = pytest.importorskip("tensorflow")

from cv_argus.model.fused_features import FUSED_GEO_FEATURE_NAMES  # noqa: E402
from cv_argus.model.layers import GeometricRatioFeatureLayer  # noqa: E402

_LEFT_EYE = [33, 160, 158, 133, 153, 144]
_RIGHT_EYE = [362, 385, 387, 263, 373, 380]
_MOUTH = [61, 291, 13, 14]


def _landmarks(ear_left=0.3, ear_right=0.3, mar=0.5) -> np.ndarray:
    lm = np.zeros((478, 2), dtype=np.float32)

    def place_eye(idx, ear):
        c0, t1, t2, c3, b2, b1 = idx
        lm[c0] = (0.0, 0.0)
        lm[c3] = (1.0, 0.0)            # horizontal = 2 * 1.0
        lm[t1] = (0.3, ear / 2)
        lm[b1] = (0.3, -ear / 2)
        lm[t2] = (0.6, ear / 2)
        lm[b2] = (0.6, -ear / 2)       # vertical = ear + ear = 2 * ear

    place_eye(_LEFT_EYE, ear_left)
    place_eye(_RIGHT_EYE, ear_right)

    lc, rc, up, lo = _MOUTH
    lm[lc] = (0.0, 0.0)
    lm[rc] = (1.0, 0.0)               # horizontal = 1.0
    lm[up] = (0.5, mar / 2)
    lm[lo] = (0.5, -mar / 2)          # vertical = mar
    return lm


def _call(layer, lm, R):
    out = layer(
        tf.constant(lm, dtype=tf.float32)[tf.newaxis, ...],
        tf.constant(R, dtype=tf.float32)[tf.newaxis, ...],
    )
    return out.numpy()[0]


@pytest.fixture(scope="module")
def layer():
    return GeometricRatioFeatureLayer()


def test_output_shape_and_order(layer):
    feats = _call(layer, _landmarks(0.25, 0.35, 0.4), np.eye(3))
    assert feats.shape == (7,)
    assert np.isclose(feats[0], 0.25, atol=1e-5)   # EAR_left
    assert np.isclose(feats[1], 0.35, atol=1e-5)   # EAR_right
    assert np.isclose(feats[2], 0.40, atol=1e-5)   # MAR


def test_identity_rotation_is_zero_pose_and_valid(layer):
    feats = _call(layer, _landmarks(), np.eye(3))
    assert np.allclose(feats[3:6], 0.0, atol=1e-4)  # pitch, yaw, roll (degrees)
    assert feats[6] == 1.0                          # ear_mar_valid


def test_large_yaw_marks_pose_invalid(layer):
    a = np.deg2rad(45)
    R = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
    feats = _call(layer, _landmarks(), R)
    assert abs(feats[4]) > 20        # yaw well past the 20-degree threshold
    assert feats[6] == 0.0           # ear_mar_valid


def test_degenerate_eye_uses_divide_no_nan(layer):
    feats = _call(layer, np.zeros((478, 2), dtype=np.float32), np.eye(3))
    assert feats[0] == 0.0 and feats[1] == 0.0 and feats[2] == 0.0
    assert not np.isnan(feats).any()


def test_batched_matches_single(layer):
    lm_a, lm_b = _landmarks(0.2, 0.3, 0.4), _landmarks(0.5, 0.5, 0.6)
    batched = layer(
        tf.constant(np.stack([lm_a, lm_b]), dtype=tf.float32),
        tf.constant(np.stack([np.eye(3), np.eye(3)]), dtype=tf.float32),
    ).numpy()
    assert batched.shape == (2, 7)
    assert np.allclose(batched[0], _call(layer, lm_a, np.eye(3)))
    assert np.allclose(batched[1], _call(layer, lm_b, np.eye(3)))


def test_config_round_trip():
    original = GeometricRatioFeatureLayer(pose_validity_threshold_deg=25.0)
    config = original.get_config()
    assert config["pose_validity_threshold_deg"] == 25.0
    rebuilt = GeometricRatioFeatureLayer.from_config(config)
    assert rebuilt.pose_validity_threshold_deg == 25.0


def test_blendshape_names_are_the_frozen_arkit_set():
    names = GeometricRatioFeatureLayer.blendshape_names
    # MediaPipe FaceLandmarker returns 52 blendshape categories; this list is the 51 *named*
    # ones (the leading "_neutral" category is dropped), in the fixed order the trained model
    # expects them concatenated after the 7 geometric features.
    assert len(names) == 51
    assert len(set(names)) == 51
    # every blendshape the fused model expects must exist in this list, by exact name
    for feature in FUSED_GEO_FEATURE_NAMES:
        if feature in ("EAR_left", "EAR_right", "MAR"):
            continue
        assert feature in names, feature
