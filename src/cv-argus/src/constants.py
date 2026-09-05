"""Single source of truth for Drive file IDs, bundle URLs, and cache filenames this container
downloads at build time.

Centralized here instead of scattered as inline defaults inside each downloader module, for two
reasons: it's the one place to look when swapping in a newer trained model, and a Drive file ID
for a file shared as "Anyone with the link" isn't a secret worth keeping out of source control
the way an API key would be — it's safe to check in as a default, still overridable via the
matching environment variable (see each downloader module for which env var reads which
constant) for anyone who wants to point at a different file without a code change.
"""

# --- CNN face-crop checkpoint (model/cnn_detector.py) ---
# Trained in notebook/07_cnn_training.ipynb: input is a single (96, 96, 3) RGB face crop. No
# longer run for its own classification in this module (that pipeline was removed — see the root
# CLAUDE.md) — kept and downloaded because FusedDrowsinessDetector reuses this exact checkpoint's
# penultimate Dense(64) layer as a frozen embedding backbone (see cnn_detector.py's
# embedding_submodel()), regardless of how many classes its own final layer was trained against.
# A real, still-open risk, unverified as of this writing: this file id must point at the same
# weights notebook/11_cnn_lstm_training_drive_pull.ipynb trained its LSTM's embeddings against
# (best_cnn_scratch_face_crops.keras, from 07's binary rerun) — if this is actually an older,
# pre-binary-migration checkpoint instead, the fused model's live embeddings won't match what its
# LSTM learned on, a silent accuracy bug, not a crash. See src/cv-argus/CLAUDE.md's "Current
# status" for the unresolved provenance check.
CNN_MODEL_DRIVE_FILE_ID = "1lxwHXWSgvJ1rQfFMjhKt4oM1a2OFJLGk"
CNN_MODEL_FILENAME = "cnn_face_crop_model.keras"
CNN_IMG_SIZE = 96  # must match notebook/07_cnn_training.ipynb's IMG_SIZE

# --- MediaPipe Face Landmarker bundle (pipeline/downloader.py, pipeline/face_landmarker_crop_stage.py) ---
# Needed by the fused pipeline's FaceLandmarkerCropStage (478 landmarks + blendshapes + head
# pose, run in IMAGE mode on the face crop FaceDetectorCropStage produces).
FACE_LANDMARKER_BUNDLE_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/"
    "float16/latest/face_landmarker.task"
)
FACE_LANDMARKER_BUNDLE_FILENAME = "face_landmarker.task"

# Detection/presence/tracking confidence thresholds -- matches
# 01_dataset_creation_lstm.ipynb's FaceLandmarkerOptions exactly.
FACE_LANDMARKER_MIN_CONFIDENCE = 0.5

# --- MediaPipe Face Detector bundle (pipeline/downloader.py) ---
# Needed by FaceDetectorCropStage — bounding-box-only face detection, no landmarks/blendshapes,
# a different and lighter bundle from the Face Landmarker above. Same one
# notebook/06_dataset_creation_face_crops.ipynb downloads in its "MediaPipe Face Detector Setup"
# cell: BlazeFace, short_range variant, float16 precision. Public/unauthenticated like the
# Landmarker bundle, so — unlike the CNN model above — there's no "trustworthy artifact" question
# gating it.
FACE_DETECTOR_BUNDLE_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/"
    "float16/latest/blaze_face_short_range.tflite"
)
FACE_DETECTOR_BUNDLE_FILENAME = "blaze_face_short_range.tflite"

# Must match notebook 06's "Pipeline Configuration Constants" cell exactly — these sized the
# crops the CNN above was actually trained on, not just reasonable-looking defaults.
FACE_DETECTOR_MIN_DETECTION_CONFIDENCE = 0.5
FACE_DETECTOR_BBOX_MARGIN_FRAC = 0.25

# --- Fused CNN-embedding + geometric-feature + LSTM classifier (model/fused_detector.py) ---
# The model this container deploys: notebook/11_cnn_lstm_training_drive_pull.ipynb's frozen-CNN-
# embedding variant — 84.24% test accuracy / 0.8375 macro-F1 (binary Not Drowsy/Drowsy), the
# best measured result in the project — see src/cv-argus/CLAUDE.md's "Current status" for the
# full accuracy caveats (single fold, no cross-validation yet) and the outstanding blocker list.
FUSED_MODEL_DRIVE_FILE_ID = "1t_d7NDITB4Erq0rT7iYuiPCc1mPc_6Yc"
# Filename kept literal (not renamed the way cnn_face_crop_model.keras/
# lstm_geometric_feature_model.keras are) so the "<filename>.threshold.json" convention the
# notebook's operating-point cell uses keeps lining up without extra bookkeeping.
FUSED_MODEL_FILENAME = "best_cnn_lstm_frozen_embedding.keras"
FUSED_MODEL_MAX_TIMESTEPS = 100  # must match notebook 11's MAX_TIMESTEPS_IMG (20s * 5fps)
FUSED_MODEL_EMBED_DIM = 64       # the frozen CNN's penultimate Dense(64, relu) layer's width
FUSED_MODEL_NUM_GEO_FEATURES = 10  # len(model.fused_features.FUSED_GEO_FEATURE_NAMES)
FUSED_MODEL_DROWSY_INDEX = 1     # index of "Drowsy" in the model's 2-class softmax output
# The decision threshold notebook 11's operating-point cell chose on validation data and wrote
# to best_cnn_lstm_frozen_embedding.keras.threshold.json (t* = 0.57 at time of writing) — checked
# in here rather than fetched as a third Drive artifact, since it's tiny metadata about one
# specific checkpoint. Update this alongside FUSED_MODEL_DRIVE_FILE_ID whenever the checkpoint
# is retrained/re-thresholded — a stale threshold paired with a new checkpoint is a silent
# accuracy bug, not a crash.
FUSED_MODEL_THRESHOLD = 0.57

# --- Shared ---
MODEL_DIR_DEFAULT = "/app/models"
