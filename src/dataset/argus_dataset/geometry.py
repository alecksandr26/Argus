"""NumPy port of the notebooks' ``GeometricRatioFeatureLayer``.

The notebooks compute the 7 geometric features (EAR x2, MAR, head-pose pitch/yaw/roll, and a
pose-validity flag) with a ``tf.keras.layers.Layer`` (``src/cv-argus/src/model/layers.py``,
also copied verbatim into notebooks 01/02/09). That layer is pure arithmetic on values
MediaPipe already returns — landmark coordinates and the facial transformation matrix — so
here it is reimplemented in NumPy. This keeps TensorFlow (~0.5 GB resident per process) out of
every spawned worker.

``tests/test_geometry_equiv.py`` checks this against the real tf.keras layer to ``atol=1e-5``.

Functions are batch-capable: pass ``(..., 478, 2)`` landmarks and ``(..., 3, 3)`` rotation
matrices, get ``(..., 7)`` back — matching the layer's ``call`` signature.
"""

from __future__ import annotations

import numpy as np

from . import config

_RAD2DEG = 180.0 / np.pi

_LEFT_EYE = np.asarray(config.LEFT_EYE_EAR_IDX, dtype=np.int64)
_RIGHT_EYE = np.asarray(config.RIGHT_EYE_EAR_IDX, dtype=np.int64)
_MOUTH = np.asarray(config.MOUTH_MAR_IDX, dtype=np.int64)


def _dist(points: np.ndarray, i: int, j: int) -> np.ndarray:
    """Euclidean distance between selected points, batch-safe. ``points``: ``(..., k, 2)``."""
    return np.linalg.norm(points[..., i, :] - points[..., j, :], axis=-1)


def _divide_no_nan(num: np.ndarray, denom: np.ndarray) -> np.ndarray:
    """tf.math.divide_no_nan: 0 where the denominator is 0, elementwise otherwise."""
    num = np.asarray(num, dtype=np.float64)
    denom = np.asarray(denom, dtype=np.float64)
    out = np.zeros(np.broadcast_shapes(num.shape, denom.shape), dtype=np.float64)
    np.divide(num, denom, out=out, where=denom != 0)
    return out


def _ear(landmarks_xy: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """Eye Aspect Ratio. ``idx`` order: [left_corner, top_1, top_2, right_corner, bottom_2, bottom_1]."""
    p = np.take(landmarks_xy, idx, axis=-2)                # (..., 6, 2)
    vertical = _dist(p, 1, 5) + _dist(p, 2, 4)
    horizontal = 2.0 * _dist(p, 0, 3)
    return _divide_no_nan(vertical, horizontal)


def _mar(landmarks_xy: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """Mouth Aspect Ratio. ``idx`` order: [left_corner, right_corner, upper_lip, lower_lip]."""
    p = np.take(landmarks_xy, idx, axis=-2)                # (..., 4, 2)
    vertical = _dist(p, 2, 3)
    horizontal = _dist(p, 0, 1)
    return _divide_no_nan(vertical, horizontal)


def _rotation_matrix_to_euler(R: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """3x3 rotation matrix -> (pitch, yaw, roll) in degrees, with the same gimbal-lock branch
    as the tf.keras layer."""
    R = np.asarray(R, dtype=np.float64)
    r00, r10, r20 = R[..., 0, 0], R[..., 1, 0], R[..., 2, 0]
    r21, r22 = R[..., 2, 1], R[..., 2, 2]
    r11, r12 = R[..., 1, 1], R[..., 1, 2]

    sy = np.sqrt(r00 ** 2 + r10 ** 2)
    singular = sy < 1e-6

    pitch = np.where(singular, np.arctan2(-r12, r11), np.arctan2(r21, r22))
    yaw = np.arctan2(-r20, sy)
    roll = np.where(singular, np.zeros_like(r10), np.arctan2(r10, r00))
    return pitch * _RAD2DEG, yaw * _RAD2DEG, roll * _RAD2DEG


def compute_geometric_features(
    landmarks_xy: np.ndarray,
    rotation_matrix: np.ndarray,
    pose_validity_threshold_deg: float = config.POSE_VALIDITY_THRESHOLD_DEG,
) -> np.ndarray:
    """Return ``(..., 7)`` = ``[EAR_left, EAR_right, MAR, pitch, yaw, roll, ear_mar_valid]``.

    ``pitch/yaw/roll`` in degrees; ``ear_mar_valid`` is ``1.0`` when both ``|yaw|`` and
    ``|pitch|`` are within the threshold, else ``0.0``.
    """
    landmarks_xy = np.asarray(landmarks_xy, dtype=np.float64)
    ear_left = _ear(landmarks_xy, _LEFT_EYE)
    ear_right = _ear(landmarks_xy, _RIGHT_EYE)
    mar = _mar(landmarks_xy, _MOUTH)
    pitch, yaw, roll = _rotation_matrix_to_euler(rotation_matrix)

    ear_mar_valid = (
        (np.abs(yaw) < pose_validity_threshold_deg)
        & (np.abs(pitch) < pose_validity_threshold_deg)
    ).astype(np.float64)

    return np.stack([ear_left, ear_right, mar, pitch, yaw, roll, ear_mar_valid], axis=-1)


def frame_feature_row(
    landmarks_xy: np.ndarray,
    rotation_matrix: np.ndarray,
    blendshape_scores: dict[str, float],
) -> np.ndarray:
    """One frame's full 58-wide feature vector: the 7 geometric features followed by the 51
    blendshape scores in ``config.BLENDSHAPE_NAMES`` order (missing names -> 0.0). float32,
    matching the notebooks' ``row = np.concatenate([ratios, bs_scores]).astype(np.float32)``.
    """
    geo = compute_geometric_features(landmarks_xy, rotation_matrix)
    bs = np.fromiter(
        (blendshape_scores.get(name, 0.0) for name in config.BLENDSHAPE_NAMES),
        dtype=np.float64, count=len(config.BLENDSHAPE_NAMES),
    )
    return np.concatenate([geo, bs]).astype(np.float32)
