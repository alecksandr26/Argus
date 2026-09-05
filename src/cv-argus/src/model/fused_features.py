"""The 10-feature geometric subset the fused CNN-embedding + LSTM model
(`fused_detector.py`) was trained on — a different, smaller set than the full 59-feature (7
geometric + 52 blendshapes) `GeometricRatioFeatureLayer` output the retired windowed-geometric-
only LSTM path used to consume (see the root `CLAUDE.md` for why that pipeline was removed).

Source of truth: `notebook/09_dataset_creation_cnn_lstm.ipynb`'s `GEO_FEATURE_NAMES` (also
mirrored in `notebook/11_cnn_lstm_training_drive_pull.ipynb` and, as a real non-notebook
implementation, `src/dataset/argus_dataset/config.py`'s `GEO_FEATURE_NAMES` /
`src/dataset/argus_dataset/pipelines.py`'s `extract_geo_for_crop`). `FUSED_GEO_FEATURE_NAMES`
below must byte-match all three, in order — this list is baked into `best_cnn_lstm_frozen_
embedding.keras`'s expected input layout the same way `GeometricRatioFeatureLayer.
blendshape_names` is baked into the LSTM model's.

Deliberately reuses `GeometricRatioFeatureLayer.call()` for the EAR/MAR/pose math rather than
reimplementing it a second time — only 3 of that layer's 7 outputs (`EAR_left`, `EAR_right`,
`MAR`) feed into the 10-feature set here; `pitch`/`yaw`/`roll`/`ear_mar_valid` are computed but
dropped, same as `09`'s extraction does.
"""

import numpy as np
import tensorflow as tf

from .layers import GeometricRatioFeatureLayer

# The 10-feature fusion set (3 geometric ratios + 7 selected blendshapes), in this exact order.
# Must match src/dataset/argus_dataset/config.py's GEO_FEATURE_NAMES and notebook 09's/11's
# GEO_FEATURE_NAMES — see src/dataset/tests/test_fused_features_equiv.py for the automated check.
FUSED_GEO_FEATURE_NAMES: list[str] = [
    "EAR_left", "EAR_right", "MAR",
    "eyeWideRight", "eyeBlinkRight", "browOuterUpRight",
    "eyeBlinkLeft", "eyeSquintLeft", "eyeWideLeft", "eyeSquintRight",
]
NUM_FUSED_GEO_FEATURES: int = len(FUSED_GEO_FEATURE_NAMES)

# Index of the ratio-layer's 3 EAR/MAR outputs within its 7-value stack
# ([EAR_left, EAR_right, MAR, pitch, yaw, roll, ear_mar_valid]) — the first 3, in the same order
# FUSED_GEO_FEATURE_NAMES expects them.
_NUM_RATIO_FEATURES = 3


def compute_fused_geo_features(
    landmarks_xy: np.ndarray,
    rotation_matrix: np.ndarray,
    blendshape_scores: dict[str, float],
    ratio_layer: GeometricRatioFeatureLayer | None = None,
) -> np.ndarray:
    """One frame's (478, 2) landmarks + (3, 3) rotation matrix + blendshape dict -> the 10-dim
    fusion vector `FusedDrowsinessDetector.predict_frame()` needs.

    `ratio_layer` is optional so a caller that processes many frames (e.g.
    `pipeline.face_landmarker_crop_stage.FaceLandmarkerCropStage`) can construct one
    `GeometricRatioFeatureLayer()` instance once and pass it in on every call instead of
    reallocating it per frame — a fresh one is built here if omitted, matching every other
    single-shot helper in this package.
    """
    layer = ratio_layer if ratio_layer is not None else GeometricRatioFeatureLayer()
    ratios = layer(
        tf.constant(landmarks_xy, dtype=tf.float32)[tf.newaxis, ...],
        tf.constant(rotation_matrix, dtype=tf.float32)[tf.newaxis, ...],
    ).numpy()[0]  # (7,): [EAR_left, EAR_right, MAR, pitch, yaw, roll, ear_mar_valid]

    base = dict(zip(FUSED_GEO_FEATURE_NAMES[:_NUM_RATIO_FEATURES], ratios[:_NUM_RATIO_FEATURES]))
    all_feats = {**base, **blendshape_scores}
    return np.array(
        [float(all_feats.get(name, 0.0)) for name in FUSED_GEO_FEATURE_NAMES],
        dtype=np.float32,
    )


def zero_fused_geo_features() -> np.ndarray:
    """The all-zero fallback for a frame where a face crop exists but FaceLandmarker found no
    landmarks in it — matches `09`'s/`argus_dataset.pipelines.extract_geo_for_crop`'s
    no-detection behavior (the frame is kept, not dropped)."""
    return np.zeros(NUM_FUSED_GEO_FEATURES, dtype=np.float32)
