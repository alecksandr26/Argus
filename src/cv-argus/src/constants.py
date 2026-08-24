"""Single source of truth for Drive file IDs, bundle URLs, and cache filenames this container
downloads at build time.

Centralized here instead of scattered as inline defaults inside each downloader module, for two
reasons: it's the one place to look when swapping in a newer trained model, and a Drive file ID
for a file shared as "Anyone with the link" isn't a secret worth keeping out of source control
the way an API key would be — it's safe to check in as a default, still overridable via the
matching environment variable (see each downloader module for which env var reads which
constant) for anyone who wants to point at a different file without a code change.
"""

# --- CNN face-crop classifier (model/cnn_detector.py) ---
# The model this container actually downloads and deploys, per the project's current decision
# (see src/cv-argus/CLAUDE.md's "Current status"). Trained in notebook/07_cnn_training.ipynb:
# input is a single (96, 96, 3) RGB face crop, output a 3-class softmax over
# Alert/Low Vigilant/Drowsy.
#
# The ~0.8 (84.75%) accuracy this model was originally reported at is flagged in
# notebook/CLAUDE.md and the root CLAUDE.md as an unreliable majority-class-collapse artifact
# from a degenerate 6-subject test split (0.00 recall on Drowsy) — not yet revalidated on the
# full subject set. Swap this file id once a trustworthy retrain lands.
CNN_MODEL_DRIVE_FILE_ID = "1lxwHXWSgvJ1rQfFMjhKt4oM1a2OFJLGk"
CNN_MODEL_FILENAME = "cnn_face_crop_model.keras"
CNN_IMG_SIZE = 96  # must match notebook/07_cnn_training.ipynb's IMG_SIZE

# --- LSTM geometric-feature model (model/detector.py) ---
# The model this project's docs still describe as the intended long-term production model (see
# the root CLAUDE.md's "Model results and current status") — no longer required at build/run
# time now that the CNN is what's actually deployed, but not deleted either: everything under
# model/ for this path (layers.py, lstm_model.py, detector.py) still works. No checked-in
# default here, unlike the CNN above — there's no single Drive id this should point at by
# default, so `download_lstm_model()` only does anything if `MODEL_DRIVE_FILE_ID` is set
# explicitly in the environment.
LSTM_MODEL_DRIVE_FILE_ID = None
LSTM_MODEL_FILENAME = "lstm_geometric_feature_model.keras"

# --- MediaPipe Face Landmarker bundle (pipeline/downloader.py, pipeline/face_landmarker_stage.py) ---
# Needed only by the (now-optional) LSTM path (478 landmarks + blendshapes + head pose).
FACE_LANDMARKER_BUNDLE_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/"
    "float16/latest/face_landmarker.task"
)
FACE_LANDMARKER_BUNDLE_FILENAME = "face_landmarker.task"

# Detection/presence/tracking confidence thresholds -- matches
# 01_dataset_creation_lstm.ipynb's FaceLandmarkerOptions exactly.
FACE_LANDMARKER_MIN_CONFIDENCE = 0.5

# --- MediaPipe Face Detector bundle (pipeline/downloader.py) ---
# Needed by the CNN path's FaceDetectorCropStage — bounding-box-only face detection, no
# landmarks/blendshapes, a different and lighter bundle from the Face Landmarker above. Same
# one notebook/06_dataset_creation_face_crops.ipynb downloads in its "MediaPipe Face Detector
# Setup" cell: BlazeFace, short_range variant, float16 precision. Public/unauthenticated like
# the Landmarker bundle, so — unlike the CNN model above — there's no "trustworthy artifact"
# question gating it; safe to always fetch at build time regardless of which model is deployed.
FACE_DETECTOR_BUNDLE_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/"
    "float16/latest/blaze_face_short_range.tflite"
)
FACE_DETECTOR_BUNDLE_FILENAME = "blaze_face_short_range.tflite"

# Must match notebook 06's "Pipeline Configuration Constants" cell exactly — these sized the
# crops the CNN above was actually trained on, not just reasonable-looking defaults.
FACE_DETECTOR_MIN_DETECTION_CONFIDENCE = 0.5
FACE_DETECTOR_BBOX_MARGIN_FRAC = 0.25

# --- Shared ---
MODEL_DIR_DEFAULT = "/app/models"
