"""`GeometricRatioFeatureLayer`, ported verbatim from the notebook.

Source of truth: `notebook/01_dataset_creation.ipynb`, cell defining `class
GeometricRatioFeatureLayer` (search for that string — cell numbers aren't stable), plus the
`blendshape_names` class attribute set on it in that notebook's "Initial Setup and Blendshape
Names" cell. It's redefined byte-identical in `notebook/03_deployment_export.ipynb` too (per
the root `CLAUDE.md`, this class is intentionally kept in sync across three places: those two
notebooks and this file) — this file is the third copy. All three must match exactly and in
this exact order — this is the list MediaPipe's `face_blendshapes` output gets re-indexed into
(see `detector.py`), and it's also baked into the trained model's expected input layout (7
geometric features followed by these 52 blendshape scores, in this order).

Do not "clean up" or reorder anything here independently of the notebooks: this layer is also
loaded back out of the saved `.keras` artifact as a `custom_object` (see `detector.py`), so a
divergence between this file and the notebooks would silently produce wrong features instead
of an import error.
"""

import numpy as np
import tensorflow as tf


class GeometricRatioFeatureLayer(tf.keras.layers.Layer):
    """
    TensorFlow-native counterpart to compute_ear, compute_mar, and rotation_matrix_to_euler.
    Inputs:
        landmarks_xy : (batch, 478, 2)   -- normalized (x, y) coordinates
        rotation_matrix : (batch, 3, 3)  -- top‑left 3×3 block of the facial transformation matrix
    Returns:
        (batch, 7) : [EAR_left, EAR_right, MAR, pitch, yaw, roll, ear_mar_valid]
        where pitch, yaw, roll are in degrees, and ear_mar_valid is 1 if the head pose is within
        the validity threshold, else 0.
    """

    # 52 ARKit-style blendshape names, in the exact order MediaPipe's FaceLandmarker returns
    # them and the order the trained model expects them concatenated after the 7 geometric
    # features. Set as a class attribute (not per-instance) to match the notebook, where code
    # elsewhere reads `GeometricRatioFeatureLayer.blendshape_names` directly off the class.
    blendshape_names = [
        "browDownLeft", "browDownRight", "browInnerUp", "browOuterUpLeft", "browOuterUpRight",
        "cheekPuff", "cheekSquintLeft", "cheekSquintRight",
        "eyeBlinkLeft", "eyeBlinkRight",
        "eyeLookDownLeft", "eyeLookDownRight", "eyeLookInLeft", "eyeLookInRight",
        "eyeLookOutLeft", "eyeLookOutRight", "eyeLookUpLeft", "eyeLookUpRight",
        "eyeSquintLeft", "eyeSquintRight", "eyeWideLeft", "eyeWideRight",
        "jawForward", "jawLeft", "jawOpen", "jawRight",
        "mouthClose", "mouthDimpleLeft", "mouthDimpleRight", "mouthFrownLeft", "mouthFrownRight",
        "mouthFunnel", "mouthLeft", "mouthLowerDownLeft", "mouthLowerDownRight",
        "mouthPressLeft", "mouthPressRight", "mouthPucker", "mouthRight",
        "mouthRollLower", "mouthRollUpper", "mouthShrugLower", "mouthShrugUpper",
        "mouthSmileLeft", "mouthSmileRight", "mouthStretchLeft", "mouthStretchRight",
        "mouthUpperUpLeft", "mouthUpperUpRight", "noseSneerLeft", "noseSneerRight",
    ]

    def __init__(self, pose_validity_threshold_deg=20.0, **kwargs):
        super().__init__(**kwargs)
        self.pose_validity_threshold_deg = pose_validity_threshold_deg

        # Fixed landmark indices (same as used in the NumPy functions)
        self.left_eye_idx = tf.constant([33, 160, 158, 133, 153, 144], dtype=tf.int32)
        self.right_eye_idx = tf.constant([362, 385, 387, 263, 373, 380], dtype=tf.int32)
        self.mouth_idx = tf.constant([61, 291, 13, 14], dtype=tf.int32)

    def get_config(self):
        config = super().get_config()
        config.update({
            "pose_validity_threshold_deg": self.pose_validity_threshold_deg,
        })
        return config

    @classmethod
    def from_config(cls, config):
        return cls(**config)

    @staticmethod
    def _dist(points, i, j):
        """Euclidean distance between two selected points, batch‑safe."""
        a = points[..., i, :]
        b = points[..., j, :]
        return tf.norm(a - b, axis=-1)

    def _ear(self, landmarks, idx):
        """
        Eye Aspect Ratio.
        idx convention: [left_corner, top_1, top_2, right_corner, bottom_2, bottom_1]
        """
        p = tf.gather(landmarks, idx, axis=-2)          # shape: (..., 6, 2)
        vertical = self._dist(p, 1, 5) + self._dist(p, 2, 4)
        horizontal = 2.0 * self._dist(p, 0, 3)
        return tf.math.divide_no_nan(vertical, horizontal)

    def _mar(self, landmarks, idx):
        """
        Mouth Aspect Ratio.
        idx convention: [left_corner, right_corner, upper_lip, lower_lip]
        """
        p = tf.gather(landmarks, idx, axis=-2)          # shape: (..., 4, 2)
        vertical = self._dist(p, 2, 3)
        horizontal = self._dist(p, 0, 1)
        return tf.math.divide_no_nan(vertical, horizontal)

    @staticmethod
    def _rotation_matrix_to_euler(R):
        """
        Convert a 3×3 rotation matrix to pitch, yaw, roll in degrees.
        Handles the gimbal‑lock singularity gracefully.
        """
        r00, r10, r20 = R[..., 0, 0], R[..., 1, 0], R[..., 2, 0]
        r21, r22 = R[..., 2, 1], R[..., 2, 2]
        r11, r12 = R[..., 1, 1], R[..., 1, 2]

        sy = tf.sqrt(r00**2 + r10**2)
        singular = sy < 1e-6

        pitch_reg = tf.atan2(r21, r22)
        yaw = tf.atan2(-r20, sy)
        roll_reg = tf.atan2(r10, r00)

        pitch_sing = tf.atan2(-r12, r11)
        roll_sing = tf.zeros_like(roll_reg)

        pitch = tf.where(singular, pitch_sing, pitch_reg)
        roll = tf.where(singular, roll_sing, roll_reg)

        rad2deg = 180.0 / np.pi
        return pitch * rad2deg, yaw * rad2deg, roll * rad2deg

    def call(self, landmarks_xy, rotation_matrix):
        ear_left = self._ear(landmarks_xy, self.left_eye_idx)
        ear_right = self._ear(landmarks_xy, self.right_eye_idx)
        mar = self._mar(landmarks_xy, self.mouth_idx)

        pitch, yaw, roll = self._rotation_matrix_to_euler(rotation_matrix)

        ear_mar_valid = tf.cast(
            tf.logical_and(
                tf.abs(yaw) < self.pose_validity_threshold_deg,
                tf.abs(pitch) < self.pose_validity_threshold_deg
            ),
            tf.float32
        )

        return tf.stack([ear_left, ear_right, mar, pitch, yaw, roll, ear_mar_valid], axis=-1)
