"""All tunables for the dataset-creation pipeline, in one place.

Every constant here mirrors a literal in one of the Colab notebooks under ``src/notebook/``.
The notebook + cell each one comes from is named in a comment so drift is easy to spot. If you
change a value here, change it in the matching notebook too (they are the Colab reference) —
``scripts/... --status`` and the ``config_hash`` guard will refuse to resume a run whose
artifacts were built under different values, but they can't detect a silent notebook mismatch.

Nothing in this module imports TensorFlow, MediaPipe, OpenCV or pandas — it stays cheap to
import from a signal handler or a worker bootstrap.
"""

from __future__ import annotations

import hashlib
import json

# --------------------------------------------------------------------------------------------
# Labels — binary scheme (drowsy_vs_not). src/notebook/01 cell 22, 02 cell 20, 06 cell 1.
# The raw clips under raw/raw_videos/ are already binary-labelled in the filename
# (level_1 = Not Drowsy, level_2 = Drowsy), so map_level does no remapping — it just validates.
# --------------------------------------------------------------------------------------------
CLASS_NAMES: list[str] = ["Not Drowsy", "Drowsy"]
NUM_CLASSES: int = len(CLASS_NAMES)


def map_level(raw_level: int) -> int:
    """Validate a filename's ``level_<n>``. The raw tree must already be binary."""
    if raw_level == 3:
        raise ValueError(
            "level 3 in filename -- raw/raw_videos/ still uses the 3-class scheme. Collapse it "
            "to binary at the source (level_1 = Not Drowsy for old Alert + Low Vigilant, "
            "level_2 = Drowsy for old Drowsy) before running the builds."
        )
    if raw_level not in (1, 2):
        raise ValueError(
            f"Unexpected level {raw_level} in filename -- expected 1 (Not Drowsy) or 2 (Drowsy)."
        )
    return raw_level


# --------------------------------------------------------------------------------------------
# Frame sampling — shared by 01 / 02 / 06 / 09.
#   src/notebook/01 cell 80, 02 cell 68, 06 cell 7, 09 cell 6.
# --------------------------------------------------------------------------------------------
SAMPLING_FPS: int = 5
POSE_VALIDITY_THRESHOLD_DEG: float = 20.0

# EAR / MAR landmark index sets (MediaPipe FaceMesh 478-point topology).
# src/notebook/01 cell 80 / 02 cell 68, and the tf.keras layer in
# src/cv-argus/src/model/layers.py.
LEFT_EYE_EAR_IDX: tuple[int, ...] = (33, 160, 158, 133, 153, 144)
RIGHT_EYE_EAR_IDX: tuple[int, ...] = (362, 385, 387, 263, 373, 380)
MOUTH_MAR_IDX: tuple[int, ...] = (61, 291, 13, 14)

# The 7 geometric features, in output order. src/notebook/02 cell 77 (GEOMETRIC_FEATURE_NAMES).
GEOMETRIC_FEATURE_NAMES: list[str] = [
    "EAR_left", "EAR_right", "MAR", "pitch", "yaw", "roll", "ear_mar_valid",
]

# MediaPipe FaceLandmarker returns 52 ARKit blendshapes; this ordered list is the 51 the
# notebooks actually use (MediaPipe's category list omits "_neutral"), so num_features = 7 + 51
# = 58 — NOT the 59 / "52 blendshapes" some stale notebook markdown claims.
# Byte-identical to blendshape_names in src/notebook/01 cell 89, 02 cell 77, 09 cell 9, and
# src/cv-argus/src/model/layers.py.
BLENDSHAPE_NAMES: list[str] = [
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

# Per-frame feature vector width. Derived, never hardcoded — same as src/notebook/01 cell 89.
NUM_FEATURES: int = len(GEOMETRIC_FEATURE_NAMES) + len(BLENDSHAPE_NAMES)  # 7 + 51 = 58

# Named columns for the flat per-frame CSV. src/notebook/02 cell 77 (FEATURE_COLUMN_NAMES).
FEATURE_COLUMN_NAMES: list[str] = GEOMETRIC_FEATURE_NAMES + BLENDSHAPE_NAMES


# --------------------------------------------------------------------------------------------
# LSTM windowed dataset — src/notebook/01_dataset_creation_lstm.ipynb.
#   cell 80: window_configs, stride_sec, MAX_TIMESTEPS.
# --------------------------------------------------------------------------------------------
LSTM_MIN_CONTEXT_SEC: int = 1
LSTM_MAX_CONTEXT_SEC: float = 6.0
LSTM_WINDOW_CONFIGS: list[float] = [
    float(x) for x in range(int(LSTM_MIN_CONTEXT_SEC), int(LSTM_MAX_CONTEXT_SEC) + 1)
]  # [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
LSTM_STRIDE_SEC: float = 1.0
# Fixed padded window length: max_context_sec * sampling_fps = 6 * 5. History: 120 -> 60 -> 30.
LSTM_MAX_TIMESTEPS: int = int(LSTM_MAX_CONTEXT_SEC * SAMPLING_FPS)  # 30

# Flat CSV metadata columns (before the flattened t###_f## feature columns).
# Matches the META_COLS in src/notebook/01 cell 94.
LSTM_META_COLS: list[str] = [
    "subject", "level", "parent_video",
    "window_duration_sec", "n_real_frames", "dropped_frames_in_video",
]


def lstm_feature_columns() -> list[str]:
    """``t000_f00 .. t{MAX_TIMESTEPS-1}_f{NUM_FEATURES-1}``, timestep-outer / feature-inner —
    exactly the flattening src/notebook/03 reshapes back to ``(MAX_TIMESTEPS, NUM_FEATURES)``."""
    return [
        f"t{t:03d}_f{f:02d}"
        for t in range(LSTM_MAX_TIMESTEPS)
        for f in range(NUM_FEATURES)
    ]


def lstm_csv_columns() -> list[str]:
    return LSTM_META_COLS + lstm_feature_columns()


# --------------------------------------------------------------------------------------------
# Flat per-frame dataset + temporal enrichment — src/notebook/02_dataset_creation_flat.ipynb.
#   cell 108: ROLL_WINDOW_SHORT / ROLL_WINDOW_LONG / rolling_base_cols.
# --------------------------------------------------------------------------------------------
FLAT_META_COLS: list[str] = ["subject", "level", "parent_video", "frame_idx"]
FLAT_CSV_COLUMNS: list[str] = FLAT_META_COLS + FEATURE_COLUMN_NAMES

ROLL_WINDOW_SHORT: int = 5   # 1.0 s @ 5 FPS
ROLL_WINDOW_LONG: int = 15   # 3.0 s @ 5 FPS
ROLLING_BASE_COLS: list[str] = [
    "EAR_mean", "EAR_left", "EAR_right", "MAR", "pitch", "yaw", "roll",
    "eyeWideRight", "eyeWideLeft", "eyeBlinkRight", "eyeBlinkLeft",
    "eyeSquintLeft", "eyeSquintRight", "browOuterUpRight", "browOuterUpLeft",
    "browDownRight", "browDownLeft", "mouthShrugUpper", "jawOpen",
]  # 19 columns


# --------------------------------------------------------------------------------------------
# Face-crop dataset — src/notebook/06_dataset_creation_face_crops.ipynb cell 7.
# --------------------------------------------------------------------------------------------
CROP_MAX_FRAMES_PER_CLIP: int = 100        # ~first 20 s of a clip at 5 FPS
CROP_MIN_DETECTION_CONFIDENCE: float = 0.5
CROP_BBOX_MARGIN_FRAC: float = 0.25        # expand the raw bbox 25% each side
CROP_JPEG_QUALITY: int = 90

FACE_CROPS_INDEX_COLS: list[str] = [
    "subject", "level", "parent_video", "frame_idx", "sample_idx",
    "image_path", "crop_width", "crop_height",
]


# --------------------------------------------------------------------------------------------
# CNN+LSTM windowed image-sequence index — src/notebook/09_dataset_creation_cnn_lstm.ipynb.
#   cell 6: window_configs. cell 9: GEO_FEATURE_NAMES (10-feature fusion set).
# --------------------------------------------------------------------------------------------
CNNLSTM_WINDOW_CONFIGS: list[float] = [3.0, 5.0, 10.0, 20.0]
CNNLSTM_MAX_TIMESTEPS_IMG: int = int(max(CNNLSTM_WINDOW_CONFIGS) * SAMPLING_FPS)  # 100

# Minority-class window rebalancing. The raw clip pool is ~2:1 Not Drowsy : Drowsy, which
# carries straight through to the window index under the historical non-overlapping tiling
# (stride == window). To lift the window-level Drowsy share toward ~1:1 without sourcing new
# video, Drowsy (``level_2``) clips are tiled with overlap while Not Drowsy (``level_1``) clips
# stay non-overlapping. ``CNNLSTM_MINORITY_WINDOW_OVERLAP = 0.0`` restores the old
# both-classes-equal behaviour. Deliberate departure from src/notebook/09's original tiling —
# see src/dataset/CLAUDE.md. Affects ``cnn_lstm_windows`` row count only, never its schema.
CNNLSTM_MINORITY_LEVEL: int = 2                 # Drowsy
CNNLSTM_MINORITY_WINDOW_OVERLAP: float = 0.5    # 0.0 = non-overlapping (historical)

# The 10-feature fusion set — EAR/MAR plus the top-ranked blendshapes from 02's Spearman cell.
GEO_FEATURE_NAMES: list[str] = [
    "EAR_left", "EAR_right", "MAR",
    "eyeWideRight", "eyeBlinkRight", "browOuterUpRight",
    "eyeBlinkLeft", "eyeSquintLeft", "eyeWideLeft", "eyeSquintRight",
]
NUM_GEO_FEATURES: int = len(GEO_FEATURE_NAMES)

CNNLSTM_INDEX_COLS: list[str] = [
    "window_duration_sec", "n_real_frames", "start_sample_idx", "end_sample_idx",
    "image_paths", "geometric_feature_seq", "subject", "parent_video", "level",
]


# --------------------------------------------------------------------------------------------
# MediaPipe model bundles. src/notebook/01 cell 45 (landmarker), 06 cell 5 (detector).
# --------------------------------------------------------------------------------------------
FACE_LANDMARKER_URL: str = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/"
    "float16/latest/face_landmarker.task"
)
FACE_LANDMARKER_FILENAME: str = "face_landmarker.task"
FACE_DETECTOR_URL: str = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/"
    "float16/latest/blaze_face_short_range.tflite"
)
FACE_DETECTOR_FILENAME: str = "blaze_face_short_range.tflite"


# --------------------------------------------------------------------------------------------
# config_hash — resume guard. Covers only the constants that change what an artifact contains.
# If any of these move, `--resume` refuses to append to an artifact built under the old values
# (use `--reset` to rebuild, or `--force` to override).
# --------------------------------------------------------------------------------------------
_HASH_RELEVANT = {
    "SAMPLING_FPS": SAMPLING_FPS,
    "POSE_VALIDITY_THRESHOLD_DEG": POSE_VALIDITY_THRESHOLD_DEG,
    "CLASS_NAMES": CLASS_NAMES,
    "LEFT_EYE_EAR_IDX": LEFT_EYE_EAR_IDX,
    "RIGHT_EYE_EAR_IDX": RIGHT_EYE_EAR_IDX,
    "MOUTH_MAR_IDX": MOUTH_MAR_IDX,
    "BLENDSHAPE_NAMES": BLENDSHAPE_NAMES,
    "GEOMETRIC_FEATURE_NAMES": GEOMETRIC_FEATURE_NAMES,
    "LSTM_WINDOW_CONFIGS": LSTM_WINDOW_CONFIGS,
    "LSTM_STRIDE_SEC": LSTM_STRIDE_SEC,
    "LSTM_MAX_TIMESTEPS": LSTM_MAX_TIMESTEPS,
    "ROLL_WINDOW_SHORT": ROLL_WINDOW_SHORT,
    "ROLL_WINDOW_LONG": ROLL_WINDOW_LONG,
    "ROLLING_BASE_COLS": ROLLING_BASE_COLS,
    "CROP_MAX_FRAMES_PER_CLIP": CROP_MAX_FRAMES_PER_CLIP,
    "CROP_MIN_DETECTION_CONFIDENCE": CROP_MIN_DETECTION_CONFIDENCE,
    "CROP_BBOX_MARGIN_FRAC": CROP_BBOX_MARGIN_FRAC,
    "CROP_JPEG_QUALITY": CROP_JPEG_QUALITY,
    "CNNLSTM_WINDOW_CONFIGS": CNNLSTM_WINDOW_CONFIGS,
    "CNNLSTM_MINORITY_LEVEL": CNNLSTM_MINORITY_LEVEL,
    "CNNLSTM_MINORITY_WINDOW_OVERLAP": CNNLSTM_MINORITY_WINDOW_OVERLAP,
    "GEO_FEATURE_NAMES": GEO_FEATURE_NAMES,
}


def config_hash(artifact: str) -> str:
    """Short stable hash of the config that affects ``artifact``'s contents. ``artifact`` is one
    of ``lstm_windows`` / ``frame_features`` / ``face_crops`` / ``cnn_lstm_windows``."""
    payload = json.dumps(
        {"artifact": artifact, "config": _HASH_RELEVANT},
        sort_keys=True, default=list,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
