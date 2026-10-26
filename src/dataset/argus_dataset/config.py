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
    # Head-pose source (added 2026-10-19): whole-head boxes in 5 positions + per-eye boxes (gaze /
    # closed), no yawn. Used by the detection dataset only (see ROBOFLOW_HEADPOSE_PROJECTS).
    # Every box is exported as a 4-corner polygon; parts_dataset._read_yolo_labels converts them.
    {"workspace": "vidyashree", "project": "headpose-w4zuc", "version": 3,
     "listed_images": 5031, "note": "Head_down/forward/left/right/up + Eye_close/forward/left/right"},
    # More head-down sources (added 2026-10-19). xsriz: one state label per image (head_down 371,
    # head_up, seeing_left/right, focused, eyes_closed, yarning, ...). facial-movements: head
    # positions + eye/mouth/pupil/seatbelt boxes (head_down 318). Both in ROBOFLOW_HEADPOSE_PROJECTS.
    {"workspace": "karthik-madhvan", "project": "drowsiness-detection-xsriz", "version": 1,
     "listed_images": 6391, "note": "per-image driver states incl. head_down/head_up (Public Domain)"},
    {"workspace": "mdulo-v", "project": "facial-movements-detection", "version": 1,
     "listed_images": 4734, "note": "head_front/down/left/right/up + eyes, mouth, pupil, seatbelt"},
    # Head-down sources added 2026-10-26, chosen from per-class contact sheets of 9 candidates.
    # All three are in ROBOFLOW_HEAD_PITCH_CHECKED_PROJECTS: their head labels are noisier.
    # kantuk ("drowsy" in Indonesian): car cabins, face boxes + one box over BOTH eyes + a mouth
    # box (CC BY 4.0). headmovement: office webcam, face boxes labelled in Indonesian (bawah = down,
    # depan = front, kiri / kanan = left / right; Public Domain). noddingoff: side-view drivers
    # nodding off, one whole-body box per image (CC BY 4.0); the same photos as Deloitte's
    # "d2 - Nodding Off" class, without the salt-and-pepper noise that export adds.
    {"workspace": "testproject-8jvqn", "project": "kantuk-edo70", "version": 1,
     "listed_images": 3090, "note": "head_dropped/head_straight + both-eyes boxes + yawn/no_yawn"},
    {"workspace": "head-movement", "project": "headmovement", "version": 12,
     "listed_images": 1412, "note": "bawah (down) / depan (front) / kiri / kanan face boxes"},
    {"workspace": "universe-adweg", "project": "noddingoff-z8zad", "version": 1,
     "listed_images": 218, "note": "nodding-off drivers, whole-body 'Distraction' boxes"},
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
    "headpose-w4zuc": {               # one box per eye; its Head_* labels: ROBOFLOW_HEAD_CLASS_MAP
        "Eye_forward": "open_eye", "Eye_left": "open_eye", "Eye_right": "open_eye",  # = gaze
        "Eye_close": "closed_eye",    # many are downcast lids on lowered heads, not fully shut
    },
    # One whole-face box per image carrying a driver state (label_sheet, 2026-10-19). The head-
    # pose states are in ROBOFLOW_HEAD_CLASS_MAP; these say nothing about the head, so mapping
    # them to None here leaves the head to the landmarks. Eyes and yawns are all landmark labels.
    "drowsiness-detection-xsriz": {"eyes_closed": None, "yarning": None},
    "facial-movements-detection": {
        "open_eye": "open_eye", "closed_eye": "closed_eye",   # per-eye boxes
        "open_mouth": None,       # talking as often as yawning: jawOpen decides (pseudo yawn)
        "closed_mouth": None, "pupil": None, "wear_seatbelt": None, "no_seatbelt": None,
    },
    # One eye box covers both eyes (contact sheets, 2026-10-26), so the eyes come from the
    # landmarks, one box per eye. Mouth boxes are dropped too: ROBOFLOW_MOUTH_LABELS turns them
    # into a mouth state and the yawn box is drawn from the lips, the same scope as every source.
    "kantuk-edo70": {"eye_open": None, "eye_closed": None, "yawn": None, "no_yawn": None},
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
# No pseudo eyes beyond this |yaw|: the far eye is hidden and the mesh puts it on the nose/cheek
# (seen on contact sheets, 2026-10-19). Such a face then needs human eye boxes, as before.
DET_PSEUDO_EYE_MAX_YAW_DEG: float = 40.0
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
HEAD_STRAIGHT_PITCH_DEG: float = 18.0   # |pitch| ≤ this and |yaw| ≤ HEAD_STRAIGHT_YAW_DEG → straight
HEAD_STRAIGHT_YAW_DEG: float = 22.0     # in between: no head box (ambiguous band)
# Calibrated 2026-10-19 against headpose-w4zuc's human labels (pitch from these landmarks):
# Head_forward pitch p95 = 19.6°, Head_down p5 = 25.2°, so a 20° down cut keeps 0.99 of down and
# 0.96 of forward. The old 10° straight limit left 35% of human-forward heads with no head box.
# |yaw| 22° keeps 0.997 of human-forward heads and excludes 0.82 of human left/right ones.

# FaceLandmarker retries for the detection dataset, when the standard face crop gives no
# landmarks (mostly wide-open yawns and turned heads, measured 2026-10-19): wider crops at a
# lower confidence, as (margin per side, crop px). Only for real faces: when BlazeFace's box is
# ≥ DET_LM_RETRY_MAX_FACE_FRAC of the image side it's almost always an eye close-up it misread.
DET_LM_RETRY_CONFIDENCE: float = 0.2
DET_LM_RETRIES: tuple[tuple[float, int], ...] = ((DET_FACE_MARGIN_FRAC, DET_IMG_SIZE), (0.75, 256), (1.25, 320))
DET_LM_RETRY_MAX_FACE_FRAC: float = 0.6
# Bump when mp_one's output changes: the MediaPipe cache lives in .cache/det_mp_v<N>/.
DET_MP_CACHE_VERSION: int = 2
# Our own guided captures (scripts/capture_frames.py → scripts/add_own_captures.py), written as a
# Roboflow-style head-pose source without going through Roboflow. The capture tag says what the
# person was asked to do; a still tagged head-down gets a human `head_down` label only when the
# landmarks agree it is lowered (pitch ≥ OWN_HEAD_DOWN_MIN_PITCH_DEG, sign HEAD_DOWN_PITCH_SIGN)
# or the landmarks are lost; below that it is dropped (the person hadn't lowered it yet). Every
# other tag gets no label: eyes, yawns and the head come from the landmarks, as for any head-pose
# source. The floor sits below HEAD_DOWN_PITCH_DEG because a low camera makes a lowered head read
# more frontal (in v4, 452 of 1,370 human head_down heads got no head box or head_straight from
# pitch), so the tag overrides pitch's straight/none in 15–20°. Not lower: headpose-w4zuc's human
# Head_forward reaches 19.6° (p95), so a barely lowered head would teach head_down on a straight
# face. Recalibrate it per camera from add_own_captures.py's pitch summary (`--min-pitch`).
OWN_CAPTURES_PROJECT: str = "own-captures"
OWN_HEAD_DOWN_TAGS: tuple[str, ...] = ("head_down", "head_down_eyes_closed")
OWN_HEAD_DOWN_MIN_PITCH_DEG: float = 15.0
# Human head-pose labels (source label -> "head_straight" / "head_down" / None). Where an image
# has one, it REPLACES the pitch auto-label above for that image: a human looked at it, and the
# pitch thresholds were only ever a proxy. None = a pose that is neither class (turned, up,
# tilted); the image is kept for its eyes but gets no head box, like the pitch ambiguous band.
# Every class of a listed project must be mapped (hard error otherwise, with a suggested map):
# check `scripts/build_detection_dataset.py --label-sheet <project>` before deciding, as with
# ROBOFLOW_CLASS_MAP — names alone have misled us before.
ROBOFLOW_HEAD_CLASS_MAP: dict[str, dict[str, str | None]] = {
    # v3, decided from label_sheet_headpose-w4zuc.jpg (2026-10-19): every Head_* box is a whole
    # head and the names match what's in the boxes.
    "headpose-w4zuc": {
        "Head_down": "head_down", "Head_forward": "head_straight",
        "Head_left": None, "Head_right": None, "Head_up": None,   # turned / tilted back
    },
    # Dashboard-camera truck cabin; boxes are face-sized (2026-10-19 label sheet).
    "drowsiness-detection-xsriz": {
        "head_down": "head_down", "focused": "head_straight",
        "head_up": None, "seeing_left": None, "seeing_right": None,
        "eyes_closed_head_left": None, "eyes_closed_head_right": None,
    },
    # Whole-head boxes incl. hair. A few head_down boxes are tiny boxes on an eye of a straight
    # face: DET_HUMAN_HEAD_MIN_REL drops those.
    "facial-movements-detection": {
        "head_down": "head_down", "head_front": "head_straight",
        "head_left": None, "head_right": None, "head_up": None,
    },
    # scripts/add_own_captures.py writes only head_down boxes (the BlazeFace face box).
    OWN_CAPTURES_PROJECT: {"head_down": "head_down"},
    # Contact sheets 2026-10-26. kantuk's head_dropped is mostly real lowered heads but also
    # yawning or laughing level heads; the pitch check drops those.
    "kantuk-edo70": {"head_dropped": "head_down", "head_straight": "head_straight"},
    # Indonesian labels. "-" = faces looking up into a low camera, "r" = a rolled (tilted) head.
    "headmovement": {"bawah": "head_down", "depan": "head_straight",
                     "kiri": None, "kanan": None, "-": None, "r": None},
    # Every image is a driver nodding off; the box is the whole body (image-level label).
    "noddingoff-z8zad": {"Distraction": "head_down"},
}
# Head-pose sources whose human head labels must agree with the landmarks, because their labels
# were seen to be noisy. A human head_down needs pitch ≥ DET_HUMAN_HEAD_DOWN_MIN_PITCH_DEG and a
# human head_straight needs the landmarks to say head_straight too. Otherwise the whole image is
# rejected (head_label_disagrees), not relabelled from pitch: in a side view a real nod can read
# as a low pitch, so the pitch guess would be the wrong label there. Without landmarks the human
# label stands, like own_captures (strongly lowered heads are what loses the mesh).
ROBOFLOW_HEAD_PITCH_CHECKED_PROJECTS: tuple[str, ...] = ("kantuk-edo70", "headmovement", "noddingoff-z8zad")
# The same floor as own_captures and for the same reasons (a low camera flattens a lowered head;
# human Head_forward reaches 19.6°).
DET_HUMAN_HEAD_DOWN_MIN_PITCH_DEG: float = OWN_HEAD_DOWN_MIN_PITCH_DEG
# Sources whose head label belongs to the whole image, not to its box (noddingoff boxes the whole
# body). The label goes on the BlazeFace face; the box's size and position are ignored, and an
# image where BlazeFace finds no face is rejected (no crop around a body box).
ROBOFLOW_IMAGE_LEVEL_HEAD_PROJECTS: tuple[str, ...] = ("noddingoff-z8zad",)
# Beyond this |yaw| the pitch check is skipped for that project: in profile the landmark pitch
# is meaningless. Measured 2026-10-26: noddingoff's pitch-rejected images were all real nods at
# yaw 37–64° reading pitch ≈ 0 (contact sheet). Only for a source whose every image was checked
# to be head-down; kantuk would let a turned, level yawning face through (one read p−20 y−32).
DET_HEAD_CHECK_MAX_YAW_DEG_BY_PROJECT: dict[str, float] = {"noddingoff-z8zad": 30.0}
# Roboflow's per-box brightness augmentation ("bbbrightness") brightens or darkens only the
# inside of each box, leaving a rectangle on the face that a detector could learn as "a head".
# Images of these projects whose box edges show a straight brightness step larger than
# DET_BOX_BRIGHTNESS_MAX_STEP (grey levels, see box_brightness_step) are rejected. Measured on
# headmovement v12 (2026-10-26): its unaugmented valid split has a median step of 1 (95th pct 4),
# its augmented train split a median of 37, and 94% of train is above 3. Every headmovement
# version from v6 on uses it; v5, the last clean one, has only 250 images.
ROBOFLOW_BOX_BRIGHTNESS_AUGMENTED_PROJECTS: tuple[str, ...] = ("headmovement",)
DET_BOX_BRIGHTNESS_MAX_STEP: float = 5.0
# A human head label counts only if its box side is at least this fraction of BlazeFace's face
# box side (catches head labels drawn on an eye).
DET_HUMAN_HEAD_MIN_REL: float = 0.5
# Crop margin around a human head box when BlazeFace misses, per project (default
# DET_HEAD_BOX_MARGIN_FRAC). xsriz boxes are face-sized like BlazeFace's, so they get the face margin.
DET_HEAD_BOX_MARGIN_BY_PROJECT: dict[str, float] = {
    "drowsiness-detection-xsriz": DET_FACE_MARGIN_FRAC,
    "kantuk-edo70": DET_FACE_MARGIN_FRAC, "headmovement": DET_FACE_MARGIN_FRAC,   # face-sized too
}
# Head-pose sources: parts_dataset.py skips them (their point is the head boxes, and they have
# no yawn labels); the detection dataset pseudo-labels any eyes they left unlabelled and, if
# BlazeFace misses the face (strong pose is the point of this data), crops around the human head
# box instead. Their eye boxes, if any, go through ROBOFLOW_CLASS_MAP as usual.
ROBOFLOW_HEADPOSE_PROJECTS: tuple[str, ...] = (
    "headpose-w4zuc", "drowsiness-detection-xsriz", "facial-movements-detection", OWN_CAPTURES_PROJECT,
    "kantuk-edo70", "headmovement", "noddingoff-z8zad")
# Head-pose images whose eyes can't be (fully) labelled — no landmarks on a turned/lowered head —
# are kept with just their head box. Trade-off: a visible-but-unlabelled eye is learned as
# background, but rejecting them would throw away exactly the hard head_down examples.
DET_HEADPOSE_ALLOW_INCOMPLETE_EYES: bool = True
# Landmark-only sources: images whose own labels are ignored entirely. Every box comes from
# FaceLandmarker on the BlazeFace crop: the head class from pitch/yaw (HEAD_*_DEG), eyes from
# DET_PSEUDO_EYE_*, a yawn from DET_PSEUDO_YAWN_*. For face/driver photos that add head poses
# (above all head_down) without usable part labels. An image without landmarks is rejected.
ROBOFLOW_LANDMARK_ONLY_PROJECTS: tuple[str, ...] = ()
# Pseudo-label yawns from the FaceLandmarker jawOpen blendshape, so an open mouth nobody boxed
# isn't learned as background. A human yawn box always wins. jawOpen > OPEN with no yawn box → a
# yawn box (the outer-lip box scaled by W/H_SCALE). Sources that label no yawns (head-pose without
# a yawn class, landmark-only) also reject jawOpen in CLOSED..OPEN as unclear; sources with human
# yawn labels never reject on it. Calibrated 2026-10-19 on 7,123 face crops with 2,355 human yawn
# boxes (aunvg, axuoa, jhmld, luvay) and contact sheets:
#   > 0.7 unlabelled: clear yawns the annotators skipped (mostly jhmld);
#   0.6-0.7: talking / surprise, some yawns;  0.5-0.6: mostly closed or beard-hidden mouths;
#   < 0.5: not yawns. But some very wide yawns read ≈ 0 (the mesh closes the mouth), so a low
#   jawOpen never removes a human yawn box.
# Human yawn box / lip box: width × 1.29, height × 1.37 (medians).
# Where a source has human mouth-state labels, they decide instead of jawOpen (on
# facial-movements, 833 jawOpen "yawns" were on human closed_mouth images):
#   "closed": no yawn;  "yawn": a yawn, boxed from the lips (no landmarks → reject);
#   "open": a yawn only if jawOpen > OPEN, unclear (reject) in CLOSED..OPEN.
# Key "" = an image carrying none of the listed labels (xsriz labels one state per image, so
# "not yarning" means not yawning).
ROBOFLOW_MOUTH_LABELS: dict[str, dict[str, str]] = {
    "drowsiness-detection-xsriz": {"yarning": "yawn", "": "closed"},
    "facial-movements-detection": {"closed_mouth": "closed", "open_mouth": "open"},
    "kantuk-edo70": {"yawn": "yawn", "no_yawn": "closed"},
}
DET_PSEUDO_YAWNS: bool = True
DET_PSEUDO_YAWN_JAW_CLOSED: float = 0.5
DET_PSEUDO_YAWN_JAW_OPEN: float = 0.7
DET_PSEUDO_YAWN_W_SCALE: float = 1.3
DET_PSEUDO_YAWN_H_SCALE: float = 1.4
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
