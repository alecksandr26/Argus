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
# src/dataset/tests/reference_geometric_layer.py.
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
# src/dataset/tests/reference_geometric_layer.py.
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
# External datasets for the "parts" architecture (eye/mouth crops → tiny CNNs). Pulled by
# scripts/fetch_mrl_eye.py and scripts/fetch_roboflow_parts.py into raw/, catalogued in
# raw/datasets_manifest.json. Download-only — nothing here changes an existing artifact, so none
# of these are in _HASH_RELEVANT below (adding them would invalidate finished 01/02/06/09 runs).
# --------------------------------------------------------------------------------------------
# MRL Eye (Fusek, ISVC 2018) — https://mrl.cs.vsb.cz/eyedataset.html. Infrared grayscale eye
# crops, one per image, labels in the filename. The page states no licence (citation only), so
# the manifest records it as unverified.
MRL_EYE_URL: str = "http://mrl.cs.vsb.cz/data/eyedataset/mrlEyes_2018_01.zip"
MRL_EYE_ZIP_NAME: str = "mrlEyes_2018_01.zip"
MRL_EYE_EXPECTED_IMAGES: int = 84_898
MRL_EYE_EXPECTED_SUBJECTS: int = 37
MRL_EYE_LICENCE: str = "unverified (no licence on dataset page; cite Fusek, ISVC 2018)"

# Roboflow Universe projects with eye-state / yawn (and some head) boxes, RGB. Versions are pinned to
# what was pulled on 2026-10-17 (version: None = "latest at download time", recorded in the
# manifest). listed_images is the Universe listing; exports are ~3x larger (augmented copies).
ROBOFLOW_EXPORT_FORMAT: str = "yolov8"
ROBOFLOW_PROJECTS: tuple[dict, ...] = (
    {"workspace": "drowsiness-74wtl", "project": "drowsiness-axuoa", "version": 16,
     "listed_images": 820, "note": "7 classes: eyes + yawn + head"},
    {"workspace": "national-university-of-technology", "project": "drowsiness-aunvg",
     "version": 1, "listed_images": 990,
     "note": "closed-eyes, open-eyes, tired, yawn"},
    {"workspace": "projects-7idri", "project": "drowsiness-detection-luvay", "version": 2,
     "listed_images": 2055, "note": "Closed-Eye, Open-Eye, Yawn"},
    {"workspace": "drowsiness-detection-system", "project": "drowsiness-driver-jhmld",
     "version": 1, "listed_images": 9700,
     "note": "11 classes, eye + mouth states; check for duplicate/inconsistent classes"},
    {"workspace": "new-project-sfrql", "project": "drowsiness-hgjay", "version": 1,
     "listed_images": 2069, "note": "face, close_eyes, open_eyes, yawn"},
    # Head-pose-only source (added 2026-10-19): whole-head boxes labelled with several head
    # positions, no eye/yawn boxes. Used by the detection dataset only (see
    # ROBOFLOW_HEADPOSE_PROJECTS); unpinned until its first pull records a version.
    {"workspace": "vidyashree", "project": "headpose-w4zuc", "version": None,
     "listed_images": None, "note": "head-pose classes (multiple head positions), head boxes only"},
)

# Merged parts dataset (scripts/build_parts_dataset.py → processed/parts_dataset/). Every source
# label is mapped onto three classes or dropped (None). Decided from per-class contact sheets of
# the real boxes, not from names: several projects reuse a name for a different box scope.
PARTS_CLASSES: tuple[str, ...] = ("open_eye", "closed_eye", "yawn")
PARTS_CLASS_PART: dict[str, str] = {"open_eye": "eye", "closed_eye": "eye", "yawn": "mouth"}
ROBOFLOW_CLASS_MAP: dict[str, dict[str, str | None]] = {
    "drowsiness-axuoa": {
        "open_eyes": "open_eye", "closed_eyes": "closed_eye", "yawn": "yawn",
        "no_yawn": None,        # closed mouth — not one of the three classes
        "obstacle": None,       # sunglasses
        "straight_head": None, "tilted_head": None,  # whole-head boxes; pose comes from landmarks
    },
    "drowsiness-aunvg": {
        "open-eyes": "open_eye", "closed-eyes": "closed_eye", "yawn": "yawn",
        "tired": None,          # half-open eyes, ambiguous
    },
    "drowsiness-detection-luvay": {
        "Open-Eye": "open_eye", "Closed-Eye": "closed_eye", "Yawn": "yawn",  # eye boxes = whole image
    },
    "drowsiness-driver-jhmld": {
        "Attentive eye": "open_eye", "open": "open_eye",
        "Drowsy eye": "closed_eye",   # samples show shut lids
        "close": "closed_eye",
        "yawn": "yawn",
        "Open-Mouth": "yawn",         # samples are wide-open yawning mouths
        "Eyeclosed": None, "Yawn": None, "noYawn": None,  # whole-face boxes, not part crops
        "closed": None,               # boxes the nose bridge between both eyes
        "asleep": None,               # mixed / unreadable
    },
    "drowsiness-hgjay": {
        "open_eyes": "open_eye", "close_eyes": "closed_eye",
        "yawn": None,                 # whole-face boxes
        "face": None,
    },
}
PARTS_CROP_PAD_FRAC: float = 0.15       # grow each box 15% per side, then square it about its centre
PARTS_MIN_CROP_PX: int = 12             # drop crops whose short side is smaller than this
PARTS_SPLIT_FRACS: tuple[float, float, float] = (0.70, 0.15, 0.15)  # train / valid / test, by group
PARTS_DHASH_SIZE: int = 8               # 64-bit difference hash — cross-project duplicate grouping

# Infrared simulation (the cabin camera is a Pi NoIR OV5647 with IR LEDs). Applied to RGB
# sources only, never to MRL (already IR) and never at inference. Red is the visible channel
# closest to NIR skin/eye appearance; the result is then pulled toward MRL's real-IR intensity
# distribution by histogram matching, blended at IR_HIST_MATCH_STRENGTH.
IR_CHANNEL_WEIGHTS_BGR: tuple[float, float, float] = (0.05, 0.15, 0.80)
IR_HIST_MATCH_STRENGTH: float = 0.5
IR_REF_SAMPLE_IMAGES: int = 5000        # MRL images sampled to build the reference histogram

# Detection dataset (scripts/build_detection_dataset.py → processed/parts_det/ + parts_det.zip).
# ONE format for every detector notebook: YOLO detection labels on square **face crops** (the
# input the edge will see after BlazeFace), plus MRL / eye-or-mouth close-ups as "closeup" images.
DET_CLASSES: tuple[str, ...] = ("open_eye", "closed_eye", "yawn", "head_straight", "head_down")
DET_IMG_SIZE: int = 192                 # stored face-crop side (px); notebooks resize to 160
DET_FACE_MARGIN_FRAC: float = CROP_BBOX_MARGIN_FRAC  # 25% per side, same as cv-argus / 06
DET_MIN_BOX_FRAC: float = 0.02          # drop boxes thinner than 2% of the crop after clipping
DET_MIN_VISIBLE_FRAC: float = 0.5       # drop boxes with < 50% of their area inside the crop
DET_CLOSEUP_MIN_BOX: float = 0.6        # no face + every box ≥ 60% of the image → close-up image
DET_REQUIRE_BOTH_EYES: bool = True      # face images need ≥2 eye boxes (else eyes go unlabelled)
# Pseudo-label eyes the annotators skipped (e.g. yawn-only images) from FaceLandmarker, so they
# aren't learned as background. Calibrated on 4,071 human-labelled eyes (2026-10-17): landmark
# contour centres sit within 13% of an eye-width of the human box (median); human boxes are ~1.4x
# the contour width; open-eye EAR p10 = 0.21, closed-eye EAR p75 = 0.17.
DET_PSEUDO_EYES: bool = True
DET_PSEUDO_EYE_W_SCALE: float = 1.4     # pseudo box width = 1.4 x landmark contour width
DET_PSEUDO_EYE_ASPECT: float = 0.75     # pseudo box height = 0.75 x its width
DET_PSEUDO_EAR_CLOSED: float = 0.15     # EAR below → closed_eye
DET_PSEUDO_EAR_OPEN: float = 0.25       # EAR above → open_eye; in between → reject image
DET_PSEUDO_MATCH_DIST: float = 0.5      # an eye counts as labelled if a box centre is within
                                        # 0.5 eye-widths of the landmark eye centre
DET_MRL_MAX_PER_SUBJECT_CLASS: int = 300  # cap MRL so it doesn't swamp the ~10k Roboflow faces
DET_JPEG_QUALITY: int = 95
DET_SAME_ORIGINAL_CORR: float = 0.75    # stem-mates join one group only if their 16x16 thumbnails
                                        # correlate ≥ this (flip-invariant); see detection_dataset
# Projects whose original filenames collide across unrelated images (merged from several
# sources). Measured: jhmld stem-mate similarity is bimodal (≈1.0 true copies vs ≈0.2 collisions).
# Single-source projects keep plain stem grouping — their heavy augmentations (crop/rotate) make
# true copies look dissimilar (axuoa median 0.35), so a similarity test would split them apart.
DET_STEM_UNRELIABLE_PROJECTS: tuple[str, ...] = ("drowsiness-driver-jhmld",)
DET_MP_CHUNK: int = 250                 # images per cached MediaPipe chunk (resume granularity)
# Head position from FaceLandmarker pitch (degrees, geometry._rotation_matrix_to_euler). The sign
# of "looking down" was checked on a contact sheet of real faces — see HEAD_DOWN_PITCH_SIGN.
HEAD_DOWN_PITCH_SIGN: int = 1           # +1: positive pitch = head down; -1: negative = down
HEAD_DOWN_PITCH_DEG: float = 20.0       # |pitch| beyond this, in the down direction → head_down
HEAD_STRAIGHT_PITCH_DEG: float = 10.0   # |pitch| ≤ this and |yaw| ≤ HEAD_STRAIGHT_YAW_DEG → straight
HEAD_STRAIGHT_YAW_DEG: float = 25.0     # in between: no head box (ambiguous band)
# Human head-pose labels (source label -> "head_straight" / "head_down" / None). Where an image
# has one, it REPLACES the pitch auto-label above for that image: a human looked at it, and the
# pitch thresholds were only ever a proxy. None = a pose that is neither class (turned, up,
# tilted); the image is kept for its eyes but gets no head box, like the pitch ambiguous band.
# Every class of a listed project must be mapped (hard error otherwise, with a suggested map):
# check `scripts/build_detection_dataset.py --label-sheet <project>` before deciding, as with
# ROBOFLOW_CLASS_MAP — names alone have misled us before.
ROBOFLOW_HEAD_CLASS_MAP: dict[str, dict[str, str | None]] = {
    "headpose-w4zuc": {},   # fill in after the first fetch (class names aren't public without a key)
}
# Projects that carry ONLY head boxes. parts_dataset.py skips them (nothing to crop); the
# detection dataset pseudo-labels their eyes from landmarks and, if BlazeFace misses the face
# (strong pose is the point of this data), crops around the human head box instead.
ROBOFLOW_HEADPOSE_PROJECTS: tuple[str, ...] = ("headpose-w4zuc",)
# Head-pose images whose eyes can't be (fully) labelled — no landmarks on a turned/lowered head —
# are kept with just their head box. Trade-off: a visible-but-unlabelled eye is learned as
# background, but rejecting them would throw away exactly the hard head_down examples.
DET_HEADPOSE_ALLOW_INCOMPLETE_EYES: bool = True
# Crop margin around a human head box (no-face fallback). Smaller than DET_FACE_MARGIN_FRAC
# because a head box already includes hair/chin, so the crop ends up about the same scale.
DET_HEAD_BOX_MARGIN_FRAC: float = 0.10
# Whole images are rejected when they carry a label that marks an eye/mouth state we can't map
# to a part box — otherwise that eye/mouth would be learned as background.
ROBOFLOW_REJECT_IMAGE_LABELS: dict[str, tuple[str, ...]] = {
    "drowsiness-aunvg": ("tired",),
    "drowsiness-axuoa": ("obstacle",),
    "drowsiness-driver-jhmld": ("closed", "asleep", "Yawn"),
    "drowsiness-hgjay": ("yawn",),
}


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
