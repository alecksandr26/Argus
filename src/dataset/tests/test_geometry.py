"""NumPy geometry port — self-consistency checks that need no TensorFlow."""

import numpy as np

from argus_dataset import config, geometry


def _synthetic_landmarks(ear_left=0.3, ear_right=0.3, mar=0.5):
    """478x2 landmark array with the EAR/MAR index points placed to hit target ratios.
    All other points are 0; only the gathered indices matter."""
    lm = np.zeros((478, 2), dtype=np.float64)

    def place_eye(idx, ear):
        # idx order: [left_corner, top_1, top_2, right_corner, bottom_2, bottom_1]
        c0, t1, t2, c3, b2, b1 = idx
        lm[c0] = (0.0, 0.0)
        lm[c3] = (1.0, 0.0)            # horizontal = 2 * 1.0
        # vertical = dist(t1,b1) + dist(t2,b2) = 2 * ear  ->  each pair separated by `ear` in y
        lm[t1] = (0.3, ear / 2)
        lm[b1] = (0.3, -ear / 2)
        lm[t2] = (0.6, ear / 2)
        lm[b2] = (0.6, -ear / 2)

    place_eye(config.LEFT_EYE_EAR_IDX, ear_left)
    place_eye(config.RIGHT_EYE_EAR_IDX, ear_right)

    lc, rc, up, lo = config.MOUTH_MAR_IDX
    lm[lc] = (0.0, 0.0)
    lm[rc] = (1.0, 0.0)               # horizontal = 1.0
    lm[up] = (0.5, mar / 2)
    lm[lo] = (0.5, -mar / 2)          # vertical = mar
    return lm


def test_ear_mar_values_and_order():
    lm = _synthetic_landmarks(ear_left=0.25, ear_right=0.35, mar=0.4)
    feats = geometry.compute_geometric_features(lm, np.eye(3))
    assert feats.shape == (7,)
    assert np.isclose(feats[0], 0.25, atol=1e-9)   # EAR_left
    assert np.isclose(feats[1], 0.35, atol=1e-9)   # EAR_right
    assert np.isclose(feats[2], 0.40, atol=1e-9)   # MAR


def test_identity_rotation_is_zero_pose_and_valid():
    feats = geometry.compute_geometric_features(_synthetic_landmarks(), np.eye(3))
    assert np.allclose(feats[3:6], 0.0, atol=1e-9)  # pitch, yaw, roll
    assert feats[6] == 1.0                          # ear_mar_valid


def test_large_yaw_marks_invalid():
    # rotate 45 deg about the vertical axis -> |yaw| == 45 > 20 threshold
    a = np.deg2rad(45)
    R = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
    feats = geometry.compute_geometric_features(_synthetic_landmarks(), R)
    assert abs(feats[4]) > 20        # yaw
    assert feats[6] == 0.0           # ear_mar_valid


def test_divide_no_nan_on_degenerate_eye():
    lm = np.zeros((478, 2))          # all points coincident -> horizontal == 0
    feats = geometry.compute_geometric_features(lm, np.eye(3))
    assert feats[0] == 0.0 and feats[1] == 0.0 and feats[2] == 0.0


def test_frame_feature_row_width_and_dtype():
    row = geometry.frame_feature_row(
        _synthetic_landmarks(), np.eye(3), {"eyeBlinkLeft": 0.9, "jawOpen": 0.1}
    )
    assert row.shape == (config.NUM_FEATURES,) == (58,)
    assert row.dtype == np.float32
    # blendshapes start at index 7; eyeBlinkLeft is position 8 in BLENDSHAPE_NAMES
    assert np.isclose(row[7 + config.BLENDSHAPE_NAMES.index("eyeBlinkLeft")], 0.9)


def test_batched_matches_looped():
    lms = np.stack([_synthetic_landmarks(0.2, 0.3, 0.4), _synthetic_landmarks(0.5, 0.5, 0.6)])
    Rs = np.stack([np.eye(3), np.eye(3)])
    batched = geometry.compute_geometric_features(lms, Rs)
    assert batched.shape == (2, 7)
    for i in range(2):
        one = geometry.compute_geometric_features(lms[i], Rs[i])
        assert np.allclose(batched[i], one)
