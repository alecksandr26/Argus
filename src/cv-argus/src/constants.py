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

# --- "Parts" pipeline (PIPELINE=parts): tiny YOLO-style detector + rule engine ---
# notebook/12_tiny_grid_detector.ipynb's step-2 model "tiny_yolo_w1" (46k params). On the test
# face crops it scored eye-state accuracy 0.95, mAP@0.5 0.63, and int8 was as accurate as float.
# Both files are shared "Anyone with the link"; the env vars of the same name override the ids.
PARTS_INT8_DRIVE_FILE_ID = "1r5E0CqKLd6zktj-ZqhYCo2pFtf-Abniz"    # tiny_yolo_w1_20261018_2048_int8.tflite
PARTS_INT8_FILENAME = "parts_tiny_yolo_w1_int8.tflite"
PARTS_FLOAT_DRIVE_FILE_ID = "1_CIhATJl1osw_q7G-qyrxMoKMd5ojl-M"   # tiny_yolo_w1_20261018_2048.keras
PARTS_FLOAT_FILENAME = "parts_tiny_yolo_w1.keras"
PARTS_DEFAULT_PRECISION = "int8"    # PARTS_PRECISION=int8|float
# Must match the training data (src/dataset/argus_dataset/config.py DET_*) and notebook 12's IMG.
PARTS_CLASSES = ("open_eye", "closed_eye", "yawn", "head_straight", "head_down")
PARTS_IMG_SIZE = 160                # model input side
PARTS_STORED_CROP_SIZE = 192        # DET_IMG_SIZE: the crop is made at this size, then resized
PARTS_FACE_MARGIN_FRAC = 0.25       # DET_FACE_MARGIN_FRAC: square side = max(w,h) * 1.5
PARTS_CONF_THRESHOLD = 0.25         # detection score kept for tracking (= notebook CONF_THR)
PARTS_NMS_IOU = 0.5
PARTS_TFLITE_THREADS = 1            # measured fastest: 1.35 ms vs 1.69 ms at 4 threads

# Tracker (model/parts_tracker.py). At SAMPLE_FPS=5 a frame is 0.2 s.
PARTS_TRACK_IOU = 0.3               # match a detection to a track at IoU >= this
PARTS_TRACK_MAX_MISSES = 3          # a track survives 3 unmatched frames (0.6 s)
PARTS_TRACK_VOTE_FRAMES = 3         # state = majority over the last 3 frames (smooths flicker)

# Rule engine (orchestrator/parts_rules.py). Seconds; every one overridable by its env var.
# Eyes-closed ladder: closed >= LOW -> low, >= MEDIUM -> medium, >= CRITICAL -> critical.
RULE_EYES_LOW_S = 1.0
RULE_EYES_MEDIUM_S = 2.0
RULE_EYES_CRITICAL_S = 3.0
# After a yawn within RULE_YAWN_RECENT_S, the same ladder fires earlier (fatigue context).
RULE_YAWN_RECENT_S = 10.0
RULE_EYES_LOW_YAWN_S = 0.7
RULE_EYES_MEDIUM_YAWN_S = 1.3
RULE_EYES_CRITICAL_YAWN_S = 2.0
# Head down: with eyes closed/not visible -> critical; with eyes open (looking down) -> medium.
RULE_HEAD_DOWN_S = 3.0
RULE_HEAD_DOWN_YAWN_S = 2.0         # head down + eyes closed/not visible after a recent yawn
# A yawn *event* = mouth open this long; 3 events in the window -> low "fatigue".
RULE_YAWN_EVENT_S = 1.5
RULE_YAWN_COUNT = 3
RULE_YAWN_WINDOW_S = 300.0
RULE_NO_FACE_S = 3.0                # face lost this long (after having one) -> low "not visible"
RULE_GRACE_S = 0.4                  # a gap this short doesn't reset a running timer (2 frames)
RULE_RECOVERY_S = 2.0               # severity only drops after all signals are clear this long
# Severity -> the existing reading contract (ESP32: p_drowsy >= 0.57 means drowsy).
RULE_P_DROWSY = {"none": 0.05, "low": 0.30, "medium": 0.75, "critical": 0.95}

# --- Capture / runtime ---
# Frames per second the pipeline samples from the camera (see pipeline/sources.py +
# main.py's SAMPLE_FPS env var). Default 5 to match how the deployed model was trained:
# src/dataset/argus_dataset/config.py's SAMPLING_FPS = 5, and FUSED_MODEL_MAX_TIMESTEPS = 100
# is 20s * 5fps. Running inference faster feeds the LSTM's 100-frame window at a denser rate
# than training, so its "20 seconds of context" shrinks -- a correctness issue, not just a
# perf knob. A live camera is decimated at the source (grab-without-decode on skipped frames)
# so the extra frames never cost CPU. SAMPLE_FPS=0 disables the cap (process every frame).
DEFAULT_SAMPLE_FPS = 5

# --- Local alert buffer (buffer/store.py) ---
# BUFFER_DIR/BUFFER_DB_FILENAME env vars are already documented in .env.example/Dockerfile/
# docker-compose.yml (the buffer-data volume) -- these are just their checked-in defaults,
# following the same MODEL_DIR_DEFAULT pattern below.
BUFFER_DIR_DEFAULT = "/app/data"
BUFFER_DB_FILENAME_DEFAULT = "buffer.sqlite3"

# --- Orchestrator reading policy (orchestrator/orchestrator.py) ---
# The ESP32 fuses the camera's class with its grip sensor and needs to see the class over time
# (5 s debounce, 20 s escalation, 10 s recovery in fusion_contract.py), so the orchestrator
# reports a *reading* on every class change plus a keepalive while it stays the same -- not a
# sparse "alert". (It used to emit only Drowsy, at most every 30 s, which left fusion unable to
# ever see a recovery.)
#
# Consecutive frames of a different class before the reported class flips, in both directions:
# 3 frames at SAMPLE_FPS=5 = ~0.6 s, enough to absorb one bad frame (a blink, a head turn).
ORCHESTRATOR_DEBOUNCE_FRAMES = 3
# Re-send the current class this often while it doesn't change. Must stay well under fusion's
# 5 s debounce and 10 s recovery windows so those can elapse between readings, and under the
# ESP32's camera-stale timeout so a live camera never looks offline.
ORCHESTRATOR_KEEPALIVE_SECONDS = 2.0
# Frames ignored after start: the LSTM window is mostly zero padding until it fills. 10 frames
# = 2 s at 5 fps.
ORCHESTRATOR_WARMUP_FRAMES = 10
# Buffer housekeeping, run from the orchestrator loop: ACKed rows older than this are deleted,
# and at most this many unsent rows are kept (oldest keepalives dropped first) through a long
# Bluetooth outage. 20000 rows ~ 11 h of 2 s keepalives.
BUFFER_KEEP_SENT_SECONDS = 24 * 3600
BUFFER_MAX_UNSENT_ROWS = 20000
BUFFER_MAINTENANCE_INTERVAL_SECONDS = 600.0
# Cadence for the RouteStatus("OK") heartbeat when no Alert is due -- lets the backend/ESP32
# distinguish "quiet because everything's fine" from "the Pi died". 60s default: frequent enough
# that a stuck/crashed Pi is noticed within about a minute, infrequent enough not to flood the
# buffer with heartbeats between real alerts (at 60s this adds at most ~1440 extra rows/day).
ORCHESTRATOR_HEARTBEAT_INTERVAL_SECONDS = 60.0
# How long the decision loop blocks on its input queue before re-checking its own stop event and
# whether a heartbeat is due -- mirrors pipeline/stage.py's DEFAULT_QUEUE_GET_TIMEOUT rationale
# (responsive shutdown without busy-looping), but this also gates heartbeat timing resolution.
ORCHESTRATOR_LOOP_POLL_SECONDS = 1.0

# --- Shared ---
MODEL_DIR_DEFAULT = "/app/models"
