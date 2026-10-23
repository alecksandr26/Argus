"""Single source of truth for Drive file IDs, bundle URLs, and cache filenames this container
downloads at build time.

Centralized here instead of scattered as inline defaults inside each downloader module, for two
reasons: it's the one place to look when swapping in a newer trained model, and a Drive file ID
for a file shared as "Anyone with the link" isn't a secret worth keeping out of source control
the way an API key would be — it's safe to check in as a default, still overridable via the
matching environment variable (see each downloader module for which env var reads which
constant) for anyone who wants to point at a different file without a code change.
"""

# --- MediaPipe Face Detector bundle (pipeline/downloader.py) ---
# Needed by FaceDetectorCropStage — bounding-box-only face detection, no landmarks/blendshapes.
# Same one the parts dataset builder (src/dataset) and notebook/06 use: BlazeFace, short_range
# variant, float16 precision. Public and unauthenticated.
FACE_DETECTOR_BUNDLE_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/"
    "float16/latest/blaze_face_short_range.tflite"
)
FACE_DETECTOR_BUNDLE_FILENAME = "blaze_face_short_range.tflite"

# Must match notebook 06's "Pipeline Configuration Constants" cell (the parts crop's own
# margin is PARTS_FACE_MARGIN_FRAC below; this one only sizes the overlay's face box).
FACE_DETECTOR_MIN_DETECTION_CONFIDENCE = 0.5
FACE_DETECTOR_BBOX_MARGIN_FRAC = 0.25

# One face (pipeline/face_detector_stage.py `FaceLock`): the largest face is locked and followed;
# every other face is ignored. A detection is the locked face when its centre is within
# FACE_LOCK_MAX_SHIFT_FRAC x the locked box's side of the locked centre and its side is within a
# factor FACE_LOCK_MAX_SCALE of it (the box is updated every matched frame, so the lock follows a
# moving driver). While the locked face is missing and another face stays visible for
# FACE_SWITCH_AFTER_S, the lock switches to the largest visible face (contracts C1.7).
FACE_SWITCH_AFTER_S = 5.0
FACE_LOCK_MAX_SHIFT_FRAC = 0.6
FACE_LOCK_MAX_SCALE = 1.6

# --- The "parts" pipeline: tiny YOLO-style detector + rule engine ---
# notebook/12_tiny_grid_detector.ipynb's model "tiny_yolo_w1" (46k params), the 2026-10-20 23:35
# run. On notebook 12's test set (int8; float within 0.01): mAP@0.5 0.756 (0.63 for the
# 2026-10-18 model it replaces), eye-state accuracy 0.920 (0.95), head_down AP@0.5 0.743 (0.25),
# head accuracy 0.994. Both files are shared "Anyone with the link"; the env vars of the same
# name override the ids.
# The filenames carry the run date ON PURPOSE: /app/models is the `model-cache` volume, and the
# downloader skips a file already cached under the same name, so reusing a name would keep a Pi
# silently running the previous model after an image rebuild.
PARTS_INT8_DRIVE_FILE_ID = "1MSnhSekHeCavvFzni41-KUBAMatnXqln"    # tiny_yolo_w1_20261020_2335_int8.tflite
PARTS_INT8_FILENAME = "parts_tiny_yolo_w1_20261020_2335_int8.tflite"
PARTS_FLOAT_DRIVE_FILE_ID = "1ivbCARbpw25k_ZDGDIeV0T3xcShAwCl4"   # tiny_yolo_w1_20261020_2335.keras
PARTS_FLOAT_FILENAME = "parts_tiny_yolo_w1_20261020_2335.keras"
PARTS_DEFAULT_PRECISION = "int8"    # PARTS_PRECISION=int8|float
# Must match the training data (src/dataset/argus_dataset/config.py DET_*) and notebook 12's IMG.
PARTS_CLASSES = ("open_eye", "closed_eye", "yawn", "head_straight", "head_down")
PARTS_IMG_SIZE = 160                # model input side
PARTS_STORED_CROP_SIZE = 192        # DET_IMG_SIZE: the crop is made at this size, then resized
PARTS_FACE_MARGIN_FRAC = 0.25       # DET_FACE_MARGIN_FRAC: square side = max(w,h) * 1.5
PARTS_CONF_THRESHOLD = 0.25         # detection score kept for tracking (= notebook CONF_THR)
# Decision thresholds inside a class pair: a detected eye is `closed_eye` when
# p_closed / (p_open + p_closed) >= PARTS_CLOSED_EYE_THRESHOLD, a head is `head_down` when
# p_down / (p_straight + p_down) >= PARTS_HEAD_DOWN_THRESHOLD. 0.5 = plain argmax; lower = more
# sensitive. Each is overridable by an env var of the same name. Swept on the parts val+test
# sets (int8, the 2026-10-18 model; not re-swept for the 2026-10-20 one): eyes are bimodal (0.5 -> 0.4: recall +0.6 pt, precision -0.5 pt); head_down is
# the weak class (per-frame recall 52-69%) and 0.5 -> 0.3 gains up to +7 pt at no precision cost.
PARTS_CLOSED_EYE_THRESHOLD = 0.40
PARTS_HEAD_DOWN_THRESHOLD = 0.30
PARTS_NMS_IOU = 0.5
# Eye-box sanity filter, applied before NMS (model/parts_detector.py `decode_grid`, mirrored in
# notebook 12's `grid_postprocess`): an eye detection is dropped when its larger side exceeds
# PARTS_EYE_MAX_FRAC of the crop side, or its centre y (crop-normalised, 0 = top) is outside
# PARTS_EYE_CY_RANGE. Real eyes on the square face crop are small and in the upper-middle band;
# the head-sized "eyes" come from training on full-image eye close-ups.
PARTS_EYE_MAX_FRAC = 0.30
PARTS_EYE_CY_RANGE = (0.15, 0.70)
PARTS_TFLITE_THREADS = 1            # measured fastest: 1.35 ms vs 1.69 ms at 4 threads

# Tracker (model/parts_tracker.py). At SAMPLE_FPS=5 a frame is 0.2 s.
PARTS_TRACK_IOU = 0.3               # match a detection to a track at IoU >= this
PARTS_TRACK_MAX_MISSES = 3          # a track survives 3 unmatched frames (0.6 s)
PARTS_TRACK_VOTE_FRAMES = 3         # state = majority over the last 3 frames (smooths flicker)
# head_down is caught on only ~half the frames, so a majority vote would rarely hold it; but a
# straight head is almost never misread as down (>99.5%), so a head track is `head_down` when at
# least this many of its last PARTS_TRACK_VOTE_FRAMES frames were.
PARTS_HEAD_DOWN_VOTES = 1

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
# Face lost (after having one): a driver slumped out of frame must never read as recovered
# (user decision 2026-10-20). Low at 3 s, an alert of its own at 5 s (medium) and 10 s (critical);
# and while the face is missing, a held severity never decays (see parts_rules).
RULE_NO_FACE_S = 3.0                # -> low "no_face"
RULE_NO_FACE_MEDIUM_S = 5.0         # -> medium "no_face"
RULE_NO_FACE_CRITICAL_S = 10.0      # -> critical "no_face"
RULE_GRACE_S = 0.4                  # a gap this short doesn't reset a running timer (2 frames)
RULE_HEAD_GRACE_S = 1.0             # longer for the head timer: head_down misses frames often
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
