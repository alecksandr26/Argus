# CLAUDE.md — src/cv-argus

This file provides guidance to Claude Code when working in `src/cv-argus`, the Raspberry Pi 5
edge module of Argus. Read the root `CLAUDE.md` first for the overall system architecture; this
file is the module-specific supplement — exact model input/output shapes, container conventions,
and notebook-fidelity requirements that would otherwise need re-deriving from the notebook every
session. When a task touches this directory, treat the details below as binding, not background.

## What this module is

The computer-vision / AI module of Argus: the code that will run on the Raspberry Pi 5 in the
truck cabin, turning camera frames into a drowsiness verdict and handing it to whatever decides
what to do about it. **It runs one pipeline: the tiny YOLO "parts" detector + tracker +
deterministic rules** (user decision, 2026-10-18: a simpler, explainable system). A per-frame
detector reports what is *visible*: eyes open or closed, a yawn, the head straight or down.
Hand-written rules with timers then decide a severity (none / low / medium / critical) and a
reason. The binary contract the ESP32 reads (`Not Drowsy` / `Drowsy`) is derived from that
severity.

**The CNN+LSTM ("fused") pipeline is gone from this module, not merely off by default.** It
lives on branch **`archive/cnn-lstm-fused`**, the last `main` that deployed it: the frozen-CNN
embedding + FaceLandmarker geometric features + LSTM classifier from
`notebook/11_cnn_lstm_training_drive_pull.ipynb`, with 84.24% accuracy / 0.8375 macro-F1 on one
subject-grouped fold. It stays the thesis's comparison point (see `notebook/CLAUDE.md`), and its
model files are no longer downloaded or baked into the image. `GeometricRatioFeatureLayer`
moved to `src/dataset/tests/reference_geometric_layer.py`, where it still guards the dataset
builder's NumPy port.

## Current status

Done and running: the container (Dockerfile, compose files, entrypoint), the packaging
(`setup.py`, `src/__init__.py`) and a real entry point (`src/main.py` + `src/__main__.py`,
runnable as `python -m cv_argus`, the Dockerfile's `CMD`).

```
camera (SAMPLE_FPS=5) → BlazeFace (FaceDetectorCropStage: every face → FaceLock picks the
                         driver's, sets face_bbox_raw + face_lock)
  → PartsInferenceStage:
      model/parts_geometry   square ×1.5 black-padded crop → gray → 160×160     [= training]
      model/parts_detector   notebook 12 tiny_yolo_w1 (46k params), int8 .tflite | float .keras
                             → 20×20 grid decode + eye-box filter + NMS (as notebook 12)
                             → pair decision thresholds (closed eye / head down)
      model/parts_geometry   remap crop-normalised boxes → frame pixels
      model/parts_tracker    IoU tracker: stable IDs, survives 3 misses, 3-frame vote
      orchestrator/parts_rules   timers → severity + reason → DetectionResult (+ extras)
                             (on a face switch: reset_signals() + tracker reset)
  → outputs: logging (one line per frame), metrics (JSONL per frame), mjpeg (overlay)
  → orchestrator bridge → readings into buffer/ (SQLite outbox) → sender/ (ESP32 Bluetooth,
    protocol v2: PULL / DLQ / report sessions)
```

**Training fidelity (binding):**
- `square_face_box` / `crop_square` are verbatim copies of `src/dataset/argus_dataset/
  detection_dataset.py`, the code that built `parts_det.zip`.
  `src/dataset/tests/test_parts_crop_equiv.py` checks the two are pixel-identical. Change both or
  neither.
- Inference only converts frames to gray. IR simulation is a training-time augmentation and is
  never applied here.
- `decode_grid` / `nms_per_class` are verbatim from notebook 12, including the **eye-box filter**
  (below), which notebook 12's `grid_postprocess(eye_filter=True)` applies too. The one
  deliberate addition is the **pair decision thresholds** (next paragraph); at 0.5 they equal the
  notebook's argmax.
- `PARTS_*` constants mirror the dataset's `DET_*` constants.

**Decision thresholds (tunable, `constants.py` + env vars of the same name):**
- `PARTS_CONF_THRESHOLD = 0.25`: is a part detected at all (score = σ(obj)·max σ(class)).
- `PARTS_CLOSED_EYE_THRESHOLD = 0.40`: an eye is `closed_eye` when
  p_closed / (p_open + p_closed) ≥ this.
- `PARTS_HEAD_DOWN_THRESHOLD = 0.30`: a head is `head_down` when p_down / (p_straight + p_down) ≥
  this.
- 0.5 is the plain argmax; lower is more sensitive. `scripts/demo.sh --eye-threshold/
  --head-threshold` set them for a run, and the overlay footer shows the active values.

Measured on the parts val+test sets with the int8 model, matching each labelled box to its best
prediction:

| | per-frame recall | precision | moving the threshold |
|---|---|---|---|
| closed eye | ~93% | ~86% | bimodal: 0.5 → 0.4 gives +0.6 pt recall for −0.5 pt precision |
| head down | 52–69% | 85–94% | 0.5 → 0.3 gives up to +7 pt recall, no precision cost |

There are only ~30 head-down faces per split, so the head numbers are noisy. The head problem
isn't the threshold, it's per-frame recall, so the tracker and rules lean sensitive:
- a head track is down once **1 of its last 3** frames was (`PARTS_HEAD_DOWN_VOTES`), instead
  of by majority;
- the head timer's grace is `RULE_HEAD_GRACE_S = 1.0` s, not 0.4 s, so missed frames between
  detections don't reset it.

This is cheap because a straight head is almost never read as down: more than 99.5% of straight
heads are classified correctly. On the real demo clip, the new settings produced no false
head-downs (all 129 head frames stayed straight). The real fix for head pose is more head-down
training data.

**Box remapping:** a crop-normalised `(u, v)` maps to frame pixel `(x0 + u·side, y0 + v·side)`.
The overlay draws on the same `frame_bgr` that inference used, at native resolution;
`DEMO_STREAM_WIDTH` resizes the *finished* image. So the boxes can't drift from the eyes. This is
tested by a round trip including faces cut off at the frame edge, and seen on real frames.

**One face (`FaceLock`, `pipeline/face_detector_stage.py`, contract C1.7).** A cab can hold a
passenger, or someone leaning in at a stop, and only the driver counts. BlazeFace reports every
face; `FaceLock` decides which one is the driver's:
- With no lock, the **largest** face is locked (nearest the camera: the driver).
- **Matching.** A detection matches a tracked box (the locked face or a bystander) when its centre
  is within `FACE_LOCK_MAX_SHIFT_FRAC` (0.6) x the tracked box's side and its side is within a
  factor `FACE_LOCK_MAX_SCALE` (1.6) of it. The tracked box then follows the detection.
- **Bystanders** are the other faces seen next to the locked one. They're carried forward by
  matching and dropped only after `FACE_LOCK_MISS_GRACE_S` (1 s) unseen, **also while the
  driver's face matches**. So a passenger the detector missed for one frame isn't later taken for
  a "new" face.
- **While the locked face is missing**, per frame:
  1. A face that matches no bystander (the driver's box jumped, or came back displaced after a
     gap) **re-acquires** the lock on that same frame: face found, no switch, no rule reset, and
     the frame is flagged `reacquired`.
  2. Only bystanders visible: no face, and the **switch timer** runs. On the switch frame (timer
     `>= FACE_SWITCH_AFTER_S` = 5 s, on the rule engine's frame timestamps) the frame still reads
     no face and is flagged `face_switched`. The lock moves to the largest face in view (all of
     them bystanders by then), used from the next frame.
  3. Nothing visible: no face. The switch timer resets only when **no bystander is still inside
     its 1 s grace**. While one is, the timer keeps running, so a one-frame passenger miss doesn't
     restart the 5 s, and the switch waits for a frame with a face to move to.
- **Take-over.** Another face at least `FACE_LOCK_TAKEOVER_SCALE` (1.25) x the locked face's side,
  in view for 5 s next to the locked face, takes the lock. This is the driver taking it back from
  a passenger, including when the passenger was alone at boot. "In view" allows gaps of either
  face up to the 1 s grace; the timer runs from the larger face's first sighting and fires only on
  a frame where both faces are visible. The take-over frame still reads the old locked face and
  is flagged `face_switched`; the new face is used from the next frame. A smaller second face is
  ignored.
- On a switch or take-over frame, `PartsInferenceStage` feeds the frame to the rules first, then
  calls `PartsRuleEngine.reset_signals()` and resets `PartsTracker`. The held severity is never
  reset (see the rules below).
- **Known limits (accepted):**
  - A stranger never seen next to the driver looks exactly like the driver coming back, so they
    re-acquire the lock at once (rule 1). The same goes for a passenger who was out of view for
    more than 1 s.
  - A passenger leaning in at 1.25x the driver's size or more for 5 s takes the lock.
- The `FACE_LOCK_*` / `FACE_SWITCH_AFTER_S` values are **code constants** in `constants.py`, not
  env vars.

**Eye-box filter** (`model/parts_detector.py`, `implausible_eye_mask`). Before NMS, an eye box is
dropped when its larger side is over `PARTS_EYE_MAX_FRAC` (0.30) of the crop side, or its centre
y (crop-normalised, 0 = top) is outside `PARTS_EYE_CY_RANGE` (0.15-0.70). The test uses the
unclipped box. The model had learned head-sized "eyes" from full-image eye labels, and those ghost
boxes could out-vote the real eyes.
- The same filter is in notebook 12's `grid_postprocess(eye_filter=True)` and in notebook 14's
  copy of it, so the notebooks score what the Pi runs.
- Notebook 12 also gained a close-up shrink, a zoom-out augmentation and training-label cleaning
  (`EYE_LABEL_MAX_FRAC` = 0.30, which must equal `PARTS_EYE_MAX_FRAC`).
- It now reports two scores: **"as deployed"** (with the filter) and **"like-for-like with
  2026-10-20"**. Only the like-for-like one compares with the deployed model's figures in the
  root `CLAUDE.md` (mAP@0.5 0.756, eye-state 0.920, head_down AP 0.743, head accuracy 0.994).
- A Colab retrain with these changes is pending (the user runs it). Until then the deployed model
  is still the 2026-10-20 one.
- `PARTS_EYE_*` are **code constants**, not env vars.

**Severity rules** (`orchestrator/parts_rules.py`; thresholds are `RULE_*` in `constants.py`, each
overridable by env var):
- **Eyes closed:** LOW 1 s / MEDIUM 2 s / **CRITICAL 3 s**. With the mouth open now or a yawn
  < 10 s ago, this becomes 0.7 / 1.3 / **2.0 s**.
- **Head down + eyes closed or hidden:** CRITICAL at 3 s (2 s after a yawn).
- **Head down with eyes open:** MEDIUM at 3 s.
- **Yawning alone** (3 yawns ≥ 1.5 s within 5 min): never above LOW.
- **No face** (after a face was seen): **CRITICAL at `RULE_NO_FACE_CRITICAL_S` = 5 s**, reason
  `no_face`, with no LOW/MEDIUM step (contract C1.5; the 3/5/10 s ladder is gone). A driver
  slumped out of frame must never read as fine. The comparison is `elapsed >=
  RULE_NO_FACE_CRITICAL_S` on purpose: with a passenger in view, the face switch (5 s) relies on
  the no-face CRITICAL firing on or before the switch frame (C1.7).
- **While the face is missing, nothing recovers:** a held severity only drops after
  `RULE_RECOVERY_S` (2 s) of a visible, clear face, so an incident stays open while the driver is
  out of frame. When it does drop, the orchestrator emits the recovery record **at once** (see
  "Contract with the ESP32").
- **Face switch: `reset_signals(self) -> None`** (contract C1.7; T2's face stage calls it once
  per switch or take-over, after feeding that frame to the rules). It resets every running timer
  (`_eyes`, `_head`, `_mouth`, `_noface`), the yawn history and the recovery timer. It never
  touches the held severity, its reason, `_trigger` or `_seen_face`. So a held CRITICAL stays
  held through a switch and needs 2 s of a clear new face to drop, and the switch itself never
  emits a transition. Keep the signature: T2 calls it through `getattr`.

Escalation is immediate; recovery takes 2 s clear. A gap of no more than 0.4 s (1.0 s for the
head timer) doesn't reset a timer. This needs an epsilon: a one-frame gap at 5 fps is
`0.4000000000000004` in float, and without it a single flicker reset the timer (found by the
tests). Only the **two best eye tracks** are judged, visible first and then by confidence. A live
run showed a ghost "closed eye" by the ear, and with ghosts counted, int8 and float agreed on only
89% of frames instead of 98%. `trigger_s` keeps the closure length visible while the alarm is
held.

**Contract with the ESP32:** the firmware alerts on the rules' **severity** (there's no grip
sensor yet, user decision 2026-10-20).
- MEDIUM and CRITICAL → `Drowsy` (`p_drowsy` 0.75 / 0.95); none and LOW → `Not Drowsy`.
- `severity`, `reason`, `trigger_s`, `timers`, `yawn_events` and `model` travel in
  `DetectionResult.extras`, which the orchestrator merges into the payload. The ESP32 uses
  `severity`, `reason` (as the alert's `alert_type`) and `model`.
- The orchestrator emits a **severity change** as a `transition: true` reading at once (medium ->
  critical too, though both are `Drowsy`), with `previous_severity` and an `episode` id that
  lasts from medium until **and including** the record that drops back to none/low. That
  recovery record is emitted immediately, not debounced behind the class debounce, and already
  carries `Not Drowsy`.
- **Which records are backend events** (contract C1.5; the Pi's Python and SQL and the ESP32's
  `pi_event_policy` use the same rule). Only `transition: true` rows count, and an unknown or
  missing `previous_severity` (null, absent, `""`, any other word) ranks as `none`:
  - **alert medium**: severity `medium` from none/low;
  - **alert critical**: severity `critical` from none/low/medium (a raise or an escalation);
  - **driver OK**: severity none/low from medium/critical;
  - everything else (keepalives, none<->low, critical -> medium, class-only changes) is not an
    event: the ESP32 uses it only for the `vigilance` of its GPS points, then ACKs it.

  `tests/test_sender_dlq.py` replays T3's shared vectors
  (`src/esp32-argus/test/vectors/event_vectors.json`) through both the Python and the SQL form;
  that test skips when the file isn't in the checkout.
- Keepalives every `ORCHESTRATOR_KEEPALIVE_SECONDS` (2 s) never reach the backend. **There is no
  heartbeat any more** (no `route_status` records): the ESP32 reports GPS every 5 s on its own.
- A reading other than `No Face` always carries both probabilities: the ESP32 drops one without.
- `ctx.detection` is `None` only when there's no face *and* no active rule. A frame with
  non-finite probabilities is treated like one with no face, and the wire JSON is strict (a
  non-finite number becomes null), so no row can carry a bare `NaN` token.

**Logs and measurements (for the report):**
- `OUTPUTS=logging` prints **one line per frame**, led by the **criticality** (not the binary
  class, which stays in the metrics record and the buffered reading):
  ```
  [pi_camera] criticality=CRITICAL (no_face 5.0s) | face=no faces=1 ignored=1 switch=4.2s | eyes=-
  mouth=- head=- | eyes_closed 0.0s head_down 0.0s | ms blazeface 6.8 crop 0.6 infer 0.7
  decode 0.3 track 0.1 rules 0.0 queue 0.3 e2e 7.2 | cpu 23% [12 30 25 26] proc 45% | ram 412MB
  sys 18%
  ```
  The face-lock tokens after `face=` (contract C1.7): `faces=<n> ignored=<n>` on every frame the
  face stage ran; ` switch=<s>s` while the switch timer runs; ` takeover=<s>s` while a larger
  face waits to take the lock; ` SWITCHED` on a switch or take-over frame; ` REACQUIRED` on a
  re-acquisition frame. Examples: `face=yes faces=2 ignored=1 takeover=1.2s`, `face=no faces=1
  ignored=1 switch=4.2s SWITCHED`, `face=yes faces=1 ignored=0 REACQUIRED`. A passenger next to
  the driver shows as `face=yes faces=2 ignored=1` with no `switch=` timer; that is how plan
  step 6.5 is checked.
  `ms` lists every pipeline step in order, so the bottleneck is visible frame by frame.
  `queue` is end-to-end time minus the measured work, i.e. time spent waiting between stages.
  `cpu` is the board's busy % (all cores, then each core); `proc` is cv-argus's own CPU in % of
  one core. `ram` is cv-argus's resident memory; `sys` is the board's RAM in use.
  - These come from `pipeline/resources.py`, stdlib-only reads of `/proc`, re-sampled every
    `RESOURCE_SAMPLE_S` (1 s).
  - CPU is left out until at least 0.2 s of real time has passed (a replayed video file can
    finish in under a second).
  - The container sees the host's cores, so the per-core list is the Pi's 4 cores.
- `OUTPUTS=metrics` (`MetricsLogOutputStage`) appends the same facts as one JSON object per frame
  to `METRICS_LOG_PATH`. That defaults to `$BUFFER_DIR/metrics.jsonl`, on the `buffer-data`
  volume. Each record also has every track (id, label, score), `timing_ms` with `face`,
  `crop`, `infer`, `decode`, `track`, `rules` and `parts_stage`, plus `queue_ms`, `e2e_ms`,
  `res` (the CPU/RAM sample above) and `face_lock` = `{faces, ignored, switch_s, takeover_s,
  switched, reacquired}` (`null` when the face stage didn't run).
- `scripts/summarize_metrics.py metrics.jsonl [--csv out]` turns the log into:
  - latency mean / p50 / p95 / p99 / max per step, plus queueing and end to end;
  - CPU (board total, each core, cv-argus's own) and RAM mean / p95 / max over distinct
    samples (`out_resources.csv` with `--csv`);
  - state shares: face found, eyes open / closed, mouth, head, severity, reason;
  - alert-episode counts.
- `scripts/demo.sh` turns on all three outputs. The per-stage `StageStats` interval lines
  (`LATENCY_LOG_INTERVAL`) still exist and carry the same phases.

**Measured** on a desktop CPU with the real models on a 128-frame face clip:
- int8: **0.6 ms** model, 1.5 ms whole stage. Float: 5.5 ms.
- BlazeFace: about 2 ms.
- int8 vs float: same severity on 98.4% of frames.

Not measured: a Pi 5, real cabin NoIR footage, and accuracy on unseen drivers (the clip came
from the detector's training data). Those timings are for the 2026-10-18 model; the deployed
2026-10-20 23:35 model is the same 46k-parameter architecture (notebook 12: 1.5 ms median int8 on
one thread). `head_down` is still the weakest class (AP@0.5 0.74, up from about 0.25, but only
122 test boxes), and head pose comes **only** from it (a user decision; no FaceLandmarker here).
The decision thresholds and the tracker's head-down vote were tuned on the 2026-10-18 model and
haven't been re-swept for the new one.

`orchestrator/`, `buffer/`, `sender/` and `alerts/` are done too, on **Bluetooth protocol v2**
(see "`orchestrator/`, `buffer/`, `sender/`, `alerts/` — done" below). A thin, always-on bridge
stage hands each frame's `DetectionResult` to the orchestrator's decision loop. That loop writes
readings (severity changes at once, debounced class changes, keepalives) into `buffer/`'s
WAL-mode SQLite outbox. `sender/` answers the ESP32's Bluetooth sessions against it: live pulls,
one-row dead-letter pulls, and reports of what the ESP32 delivered, failed or is still holding.

## Python packaging: `src/` on disk, `cv_argus` at import time

`src/` holds the code directly — `src/__init__.py`, and (once created) `src/model/`,
`src/pipeline/`, `src/orchestrator/`, `src/buffer/`, `src/sender/`, `src/alerts/` as flat
sibling subpackages, no nested `src/cv_argus/` wrapper folder. `setup.py` bridges the two names:
`package_dir={"cv_argus": "src"}` maps the *root* package name `cv_argus` onto the `src/`
directory itself, so once installed (`pip install -e .`, which the Dockerfile does), the code
is importable as `cv_argus`, `cv_argus.model`, `cv_argus.pipeline`, etc., even though none of
those names appear on disk. Each subpackage has to be added by hand to `setup.py`'s
`packages=[...]` list as it's created — it isn't auto-discovered.

`picamera2` is the answer to the CSI-camera question in "Open decisions" below — already in
`setup.py`'s `install_requires`, gated to `platform_machine in 'armv7l aarch64'` so it only
installs on the Pi itself, not a dev laptop.

## Why a Docker-first workflow

The goal is that the exact same container image and code run on a dev laptop and on the
Raspberry Pi 5 — no "works on my machine, breaks on the Pi" gap, and no need to pollute a
global/system Python with mediapipe/tensorflow to iterate locally.

- **Base image is Debian slim (`python:3.11-slim-bookworm`), not Alpine.** MediaPipe and
  TensorFlow only publish `manylinux` wheels built against glibc; Alpine's musl libc either
  fails to install them or fails at import time. Raspberry Pi OS (64-bit, Bookworm) is
  Debian Bookworm under the hood, so this one Dockerfile builds for both `linux/amd64` (a
  laptop) and `linux/arm64` (the Pi 5) without changes — `docker compose build` targets
  whatever architecture it's run on; cross-building an arm64 image from an amd64 machine
  needs `docker buildx build --platform linux/arm64` instead.
- **Model artifacts (the tiny YOLO parts detector at int8 and float, and the BlazeFace bundle)
  are baked into the image at *build* time**, not fetched at container start — the Dockerfile
  runs `python -m cv_argus.model.downloader && python -m cv_argus.pipeline.downloader` as a build
  step, with `PARTS_INT8_DRIVE_FILE_ID`/`PARTS_FLOAT_DRIVE_FILE_ID` passed in as build `ARG`s
  (see `docker-compose.yml`'s `build.args`) — both have checked-in defaults, so no `.env` file
  is required. This device has
  to be able to boot and start monitoring the driver in a moving truck that may have no signal
  right then — a runtime download dependency is a liability a build-time one isn't. Accepted
  trade-offs: a new trained model needs an image rebuild + redeploy, not just a container
  restart; `docker build`/`docker compose build` needs network access to succeed at all (there's
  no local fallback); and rebuilding doesn't reset an *existing* `model-cache` volume's contents
  (see "Model download strategy" below for that gotcha).
- **Docker Compose is used on the Pi too, not just for dev** — it's a thin wrapper around
  `docker build`/`docker run` (no separate daemon, no meaningful overhead on arm64), and
  using the same `docker-compose.yml` in both places avoids hand-retyping flags (volumes,
  device passthrough, env vars) when moving from laptop to cabin. The one difference: the
  Pi deployment should add `restart: unless-stopped` to the service so the container comes
  back up on its own after a power cycle in the truck; that's deliberately left off the dev
  compose file so a laptop container doesn't restart forever while you're iterating on it.

### Running it

```sh
cp .env.example .env   # optional -- see "Model download strategy" below, both defaults are checked in
docker compose up --build
```

Same process either way it's started: `src/main.py`'s `main()`, reachable as `python -m
cv_argus` (the Dockerfile `CMD`), `python -m cv_argus.main`, or the `cv-argus-run` console
script (`setup.py` `entry_points`). It builds one `Pipeline` (`_build_pipeline()`), starts it,
installs `SIGINT`/`SIGTERM` handlers, and blocks until a signal arrives **or** the source ends
on its own (`Pipeline.is_alive()` goes false — e.g. a video-file `CAMERA_SOURCE` hitting EOF),
then `Pipeline.stop()` joins every stage upstream-to-downstream and calls each `close()`.

Env vars `main.py` reads (all optional, all with defaults):

| var | default | effect |
|---|---|---|
| `SOURCE` | `video_capture` | `video_capture` (`cv2.VideoCapture`, driven by `CAMERA_SOURCE`) or `picamera` |
| `CAMERA_SOURCE` | `0` | int index / `/dev/videoN` / video-file path — passed straight to `cv2.VideoCapture` |
| `OUTPUTS` | `logging` | comma-separated sinks: `logging` and/or `mjpeg` (fanned out via `Stage.connect()`) |
| `SAMPLE_FPS` | `constants.DEFAULT_SAMPLE_FPS` (5) | frames/sec sampled from the camera, decimated **at the source** (`_frame_stride` for files, `grab()`-without-`retrieve()` for a live camera, `FrameRate` control for `picamera2`). 5 = the model's training rate — see the `sources.py` bullet. `0` = every frame. |
| `DEMO_STREAM_HOST`/`DEMO_STREAM_PORT` | `0.0.0.0`/`8080` | only read when `OUTPUTS` includes `mjpeg` |
| `LATENCY_LOG_INTERVAL` | `10` | seconds between `StageStats` report lines; `0` disables (see the `latency.py` bullet) |
| `LOG_LEVEL` | `INFO` | root log level; `DEBUG` adds `LoggingOutputStage`'s per-frame lines + drop-path debug logs |
| `CV_ARGUS_NUM_THREADS` | unset (Docker: `4`) | pins BLAS/OpenMP/TF/OpenCV thread pools — `cv_argus.bootstrap`, imported first in `main.py`/`__main__.py` |
| `BUFFER_DIR` / `BUFFER_DB_FILENAME` | `/app/data` / `buffer.sqlite3` | where `buffer/`'s SQLite alert queue lives — see "`buffer/`'s SQLite file needs a volume" and `buffer/store.py` |
| `SENDER_TRANSPORT` | `bluetooth` (code) / `none` (`docker-compose.yml`) / `bluez` (`docker-compose.pi.yml`) | `bluez` (`BluezProfileListener`: the host's bluetoothd listens + pairs, connections arrive over D-Bus — the Pi's choice), `bluetooth` (`BluetoothSppListener`, a raw in-process RFCOMM socket — only in the host's network namespace) or `none` (no `SenderServer`). See "Open decisions" for why the Pi can't use `bluetooth` |
| `BLUETOOTH_CHANNEL` | `4` | the RFCOMM channel served (`bluez` and `bluetooth`); must match the firmware's `PI_BT_CHANNEL` |
| `BLUETOOTH_SECURITY` | `medium` | RFCOMM `BT_SECURITY` level set on the listening socket (`low`/`medium`/`high`) — see "Device credentials" below |
| `ESP32_BT_MAC` | unset | peer allowlist for `SenderServer`; with `bluez` also the only MAC the pairing agent accepts (unset = nothing new pairs) |
| `ARGUS_TRUCK_ID`/`ARGUS_DEVICE_API_KEY`/`ARGUS_BACKEND_URL` | unset | `sender/device_config.py`'s `DeviceConfig`, from the dashboard's `argus-device.env` (`docker-compose.yml` `env_file`, runtime only) |
| `ARGUS_WIFI_SSID`/`ARGUS_WIFI_PASSWORD`/`ARGUS_GPRS_APN`/`ARGUS_GPRS_USER`/`ARGUS_GPRS_PASSWORD` | unset | optional `DeviceConfig` network fields, from `.env` (per installation), sent to the ESP32 in `CONFIG`; verbatim, not stripped |
| model-artifact overrides | see `constants.py` | `MODEL_DIR`, `*_DRIVE_FILE_ID`, `*_FILENAME`, `*_BUNDLE_URL` — see "Model download strategy" |

`RULE_*` timers and the `PARTS_*` decision thresholds read env vars of the same name. These are
**code constants, not env vars**: `FACE_LOCK_MAX_SHIFT_FRAC` (0.6), `FACE_LOCK_MAX_SCALE` (1.6),
`FACE_LOCK_MISS_GRACE_S` (1.0), `FACE_LOCK_TAKEOVER_SCALE` (1.25), `FACE_SWITCH_AFTER_S` (5.0),
`PARTS_EYE_MAX_FRAC` (0.30), `PARTS_EYE_CY_RANGE` (0.15-0.70), and the protocol/buffer values
(`BUFFER_LEASE_SECONDS`, `BUFFER_DLQ_MAX_ATTEMPTS`, `BUFFER_ARCHIVE_NOW_REASONS`,
`BUFFER_MAX_UNSENT_ROWS`, `MAX_PULL_LIMIT`, `MAX_DLQ_LIMIT`), which are contract C4 values the
ESP32 relies on.

`docker compose` passes `/dev/video0` through by default — adjust the `devices:` entry to
match your machine, or drop it when testing against a video file. `Dockerfile` sets
`ENV PYTHONUNBUFFERED=1` so `docker compose logs` (the `StageStats` lines in particular)
streams instead of arriving in delayed bursts.

**Raspberry Pi 5 simulation — on by default.** `docker-compose.yml` holds the container to a
Pi-5 envelope (`cpuset: 0-3` + `cpus: 4` + `mem_limit: 8g`, `CV_ARGUS_NUM_THREADS=4`,
`SAMPLE_FPS=5`) so latency numbers on a big dev box are meaningful. `cpuset`+`cpus` (the cgroup
limit) is what actually constrains **MediaPipe/XNNPACK** — it has no Python thread knob;
`CV_ARGUS_NUM_THREADS` only stops the *other* libraries oversubscribing within that ceiling.
Every limit is a `CV_ARGUS_*` / `SAMPLE_FPS` env override away from full-speed dev (see the
README's "Simulating the Raspberry Pi 5"). On real Pi-5 hardware the defaults are already the
actual limits — `docker-compose.pi.yml` overrides none of them.

**Outside Docker** (`pip install -e .` then `python -m cv_argus`): set `MODEL_DIR` to a
writable local path first — it defaults to the container path `/app/models`, and the trained
model + MediaPipe bundles download into it on first run rather than being baked in.

### Demo: watching it work, on a laptop or on the Pi

`OUTPUTS=logging` (the default) only prints text. To actually *see* it — a live video feed with
the drowsiness class, face box, and fps drawn on it, viewable from a browser on any device on
the same network — set `OUTPUTS=logging,mjpeg` (or just `mjpeg`) and open
`http://<host>:8080/stream`. See `pipeline/mjpeg_output_stage.py`'s module docstring before
turning this on outside a demo: **the stream has no authentication**, and this project's own
stated cargo-theft/security risk model (root `CLAUDE.md`) makes an open camera feed a real
exposure, not just a hypothetical one — don't leave `OUTPUTS` including `mjpeg` set in a real
deployment.

- **On a laptop**: `SOURCE=video_capture` (default) with `CAMERA_SOURCE` pointed at a webcam
  index, or at a recorded video file for a zero-hardware-risk fallback if a live demo's camera
  or lighting is a concern:
  ```sh
  OUTPUTS=logging,mjpeg docker compose up --build
  # then open http://localhost:8080/stream
  ```
- **On the Pi 5**, using its CSI camera: merge in the Pi-specific overlay file for the camera
  device passthrough, `SOURCE=picamera`, and `restart: unless-stopped`:
  ```sh
  OUTPUTS=logging,mjpeg docker compose -f docker-compose.yml -f docker-compose.pi.yml up --build
  # then open http://<pi-ip>:8080/stream from any device on the same network
  ```
  **`docker-compose.pi.yml`'s device passthrough is a best effort, not verified against real Pi
  5 hardware in this repo** (see "Open decisions" below) — test this path well before an actual
  presentation, not for the first time live; treat the laptop/webcam (or video file) path above
  as the reliable fallback. Pi 5 CPU performance for BlazeFace + the parts detector is also
  unmeasured — no hardware has been available to benchmark it from this codebase's sessions.

## Model download strategy

Artifacts get baked into the image at `docker build` time, each fetched by the subpackage that
depends on it. Drive file IDs and bundle URLs live in `constants.py`; every env var below falls
back to a constant there if unset (an empty-string env var, e.g. from an unset Docker build
`ARG`, counts as absent).

- `model/downloader.py`'s `download_parts_int8_model()` / `download_parts_float_model()`: notebook
  12's `tiny_yolo_w1` as int8 `.tflite` (the default `PARTS_PRECISION`) and float `.keras`.
  - They're fetched via `gdown` from checked-in Drive ids (`PARTS_INT8_DRIVE_FILE_ID` /
    `PARTS_FLOAT_DRIVE_FILE_ID`, each overridable by the env var of the same name).
  - The files are shared "Anyone with the link". That's zero credential management, at the cost
    of the files being link-accessible to anyone with the ID. If that stops being acceptable,
    move to a service account and the Drive API v3.
  - Both precisions are baked in, so switching `PARTS_PRECISION` never needs a network at
    container start.
  - If a file is missing at runtime (e.g. a stale `model-cache` volume, below),
    `PartsDetector.from_env()` downloads it.
- `pipeline/downloader.py`'s `download_face_detector_bundle()`: the MediaPipe Face Detector
  (BlazeFace, short_range/float16) `.tflite`, a public, unauthenticated download.

The Dockerfile runs `python -m cv_argus.model.downloader && python -m
cv_argus.pipeline.downloader` as one `RUN` step, then **`python -m cv_argus.model.parts_probe`**.
That loads and runs both parts models on the build machine's own architecture (arm64 when built
on the Pi) and prints `OK`/`FAILED` for each, plus how closely int8 and float agree. The build
fails only if float is broken, because then there's nothing to fall back to. A broken int8 is a
warning, and the container will run float. On a desktop amd64 build both are OK, agreeing on
97% of grid-cell classes. That's about 1 MB of models in total; the old
CNN, fused `.keras` and FaceLandmarker bundle are no longer fetched.

**Why build time, not container start:** this device needs to boot and start monitoring the
driver even if the truck has no signal at that exact moment. Accepted trade-off: a new trained
model needs an image rebuild and redeploy. `docker build` succeeds with **no `.env` file at
all**.

**Gotcha:** `/app/models` is still a volume (`model-cache`), kept as an escape hatch for
dropping a newer model into a running container. Docker only seeds a named volume from the image
the *first* time it's created empty. So an existing `model-cache` volume keeps its old files
across rebuilds, including the old CNN+LSTM files, which are now just unused bytes. Run
`docker compose down -v` (or `docker volume rm <project>_model-cache`) when a rebuild's new model
must take effect.

## `buffer/`'s SQLite file needs a volume, not a container

SQLite isn't a server — there's no daemon, no port, nothing to add as a separate
`docker-compose.yml` service. It's a library (Python's stdlib `sqlite3`) that opens a single
file directly from within whatever process calls it, so `buffer/` just reads/writes a
file path from inside the existing `cv-argus` container, same as any other local file.

The one thing that *does* need infrastructure: a container's filesystem is thrown away on
rebuild, so that file needs to live outside the container image the same way the downloaded
model does. `BUFFER_DIR` (`/app/data` by default, see `.env.example`) is a dedicated named
volume (`buffer-data`, separate from `model-cache`) for exactly this — the queue and the
model cache have different lifecycles (e.g. you may want to wipe/back up the alert queue
without touching the downloaded model, or vice versa).

**Now implemented**: `buffer/store.py`'s `Buffer` opens the connection with `PRAGMA
journal_mode=WAL` (plus `synchronous=NORMAL` and `busy_timeout=5000`) exactly for the reason
anticipated here — `orchestrator/` (writing new alerts from its own thread) and `sender/`
(reading, then marking sent from its own thread, only after an ACK) do touch the same file
from different threads. WAL alone doesn't make two Python threads' *writes* atomic with each
other, though, so a `threading.Lock` around every write method (`enqueue`/`mark_sent`) handles
that on top of it — see "`orchestrator/`, `buffer/`, `sender/`, `alerts/` — done" below.

## Planned module layout

```
src/
├── __init__.py
├── model/          # DONE — the tiny YOLO parts detector (int8/float), crop geometry, the
│                   # IoU tracker, DetectionResult, and the gdown downloader
├── pipeline/       # DONE — threaded Stage/Pipeline abstraction (stage.py), sources
│                   # (camera/video file/Pi CSI), the BlazeFace stage, PartsInferenceStage,
│                   # the logging/metrics/MJPEG sinks, the bundle download; wired into main.py
├── alerts/         # DONE — Alert data model (models.py: AlertKind, only DROWSINESS in v2,
│                   # one envelope dataclass) + serialization (serialization.py: to/from
│                   # dict/json, and age_seconds() for the serve-time age_s) — no logic, no
│                   # persistence; orchestrator/, buffer/ and sender/ all depend on this
├── orchestrator/   # DONE — OrchestratorBridgeOutputStage (bridge.py, a thin always-on
│                   # pipeline sink) + Orchestrator (orchestrator.py, its own decision-loop
│                   # thread): severity changes at once, debounced class changes, keepalives
│                   # (no heartbeat) — the "Alert Orchestration (Decision Making)" box in the
│                   # design diagram. parts_rules.py is the severity rule engine (with
│                   # reset_signals() for a face switch). fusion_contract.py is the typed
│                   # reference for the ESP32's drowsy+grip fusion: not wired into main.py.
├── buffer/         # DONE — Buffer (store.py): SQLite (WAL) with an `alerts` table (states
│                   # outbox / dlq / sent, the TAKEN lease, the round-robin DLQ) and an
│                   # `archive` table for everything given up on; status.py is the read-only
│                   # `python -m cv_argus.buffer.status`. No communication logic of its own.
└── sender/         # DONE — a Bluetooth SPP *server* for the ESP32's protocol v2 sessions
                     # (protocol.py's grammar, server.py's SenderServer accept loop +
                     # housekeeping, transport.py's Transport/Listener + fakes,
                     # bluetooth_transport.py / bluez_profile.py the real transports,
                     # device_config.py the CONFIG reply, inject.py a test reading).
                     # v2 not yet run against the real ESP32 — see "Open decisions".
```

See "`orchestrator/`, `buffer/`, `sender/`, `alerts/` — done" below for the full design of the
four newer packages.

### `model/` — done: the parts detector, its geometry and the tracker

- `parts_detector.py`: `PartsDetector`, with `decode_grid` and `nms_per_class` verbatim from
  notebook 12, plus `apply_pair_thresholds`. The int8 TFLite backend uses
  `tf.lite.Interpreter(num_threads=1)`; one thread was measured fastest (1.35 ms vs 1.69 ms on
  4). The float backend is a fixed-signature `tf.function`, traced once at startup, to avoid
  eager-dispatch overhead. TensorFlow is imported lazily, so the pure decode maths is testable
  without it.
- `parts_probe.py`: loads and runs a model file **in a child process** (`python -c`, numpy +
  tensorflow only), checking the `(20, 20, 10)` output shape and that the output is finite.
  The int8 `.tflite` has failed on a Raspberry Pi 5 while float worked, and a TFLite native
  crash (SIGILL/SIGSEGV) can't be caught by `try/except` in-process, hence the child. It's
  used in two places:
  - **Startup:** `PartsDetector.from_env()` probes int8 and loads the float `.keras` instead
    if the probe fails.
  - **Runtime:** if the int8 backend raises mid-run, `detect()` swaps to float once and retries
    the frame.

  After a fallback, `precision` reads `float` everywhere (overlay, logs, metrics) and
  `fallback_reason` says why. `PARTS_INT8_FALLBACK=0` makes int8 failures fatal again. The
  probe costs one TensorFlow import in a child at startup (a few seconds).
- `parts_geometry.py`: the square training crop and the crop → frame box remap (both pure numpy
  and cv2).
- `parts_tracker.py`: `PartsTracker`, a greedy per-kind IoU tracker with stable `E1`/`M1`/`H1`
  IDs. Tracks survive `PARTS_TRACK_MAX_MISSES` frames, and each track's state is a 3-frame vote
  (with the head-down exception above). `reset()` drops every track and restarts the IDs;
  `PartsInferenceStage` calls it on a face switch, so the old face's eyes don't carry over.
- `detector.py`: `DetectionResult` (level, probabilities, `extras`).
- `downloader.py`: the gdown fetches above. `model/__init__.py` re-exports
  `download_parts_int8_model` / `download_parts_float_model` **lazily** (a module
  `__getattr__`), so `from cv_argus.model import DetectionResult` (the rule engine needs it)
  doesn't import `gdown`. `tests/test_model_package_lazy.py` guards that.

There's no `mediapipe` import anywhere under `model/`. That boundary is deliberate:
`pipeline/` turns MediaPipe output into plain numbers (`face_bbox_raw`).

### `pipeline/` — done, a threaded `Stage`/`Pipeline` abstraction

Built as a small pipe-and-filter framework rather than one monolithic camera loop, specifically
so a future model family could share the same plumbing instead of reimplementing
threading/queueing/shutdown from scratch — the CNN, LSTM and fused pipelines all shared it
before each was replaced, and the parts pipeline reuses it unchanged:

- **`stage.py`** — `Stage` (abstract base: owns a thread, an input queue, one or more output
  queues; subclasses implement `process_item()`), `SourceStage` (no input queue; implements
  `produce()` instead), `OutputStage` (sink; implements `handle()` instead), `Pipeline` (starts/
  stops a connected group of stages together, joining upstream-to-downstream on shutdown), and
  `FrameContext` (the one item type that flows through a pipeline, accumulating fields as it
  passes through stages — a raw frame, then a face crop or landmarks, then a `DetectionResult`).
  Threads, not multiprocessing: MediaPipe's/TensorFlow's native calls release the GIL, so
  threads still get real parallelism on the actual bottleneck work, without IPC-serializing
  frames/tensors between processes. A live source (see `sources.py`) sets
  `drop_oldest_when_full=True` so a slow downstream stage can't make the whole pipeline fall
  further and further behind real time — bounded latency over completeness, since this is a
  live safety system, not a batch job. `scripts/smoke_test_pipeline.py` exercises this plumbing
  (thread lifecycle, sentinel-based shutdown, the drop-oldest backpressure path) with synthetic
  stages — no MediaPipe/TensorFlow/downloaded models needed to run it.
- **`sources.py`** — `VideoCaptureSource` (wraps `cv2.VideoCapture`; one class covers a camera
  *and* a video file, since `cv2.VideoCapture` already accepts both, matching the existing
  `CAMERA_SOURCE` convention) and `PiCameraSource` (wraps `picamera2` for the Pi's CSI camera —
  not yet verified against real Pi hardware, see "Open decisions" below). "Multiple cameras" is
  multiple `Pipeline` instances, each with its own `Source`, not one `Source` merging feeds —
  see `sources.py`'s module docstring for why.
  **Frame-rate cap (`target_fps` / `frame_rate`, default `constants.DEFAULT_SAMPLE_FPS` = 5).**
  The rule engine's timers are timestamp-based, but the tracker's miss/vote windows
  (`PARTS_TRACK_MAX_MISSES`, `PARTS_TRACK_VOTE_FRAMES`) and `RULE_GRACE_S` are tuned in frames
  at 5 fps, so the live pipeline samples at 5 fps. Decimation happens **at the source**, before
  any MediaPipe/detector work: a live camera `grab()`s every frame (cheap, no decode)
  and only `retrieve()`s + emits one per `1/target_fps`; a video file strides by
  `_frame_stride(src_fps, target_fps)` and emits a virtual frame-clock `timestamp_ms`
  (deterministic, strictly increasing — the old wall-clock stamp for a file was
  replay-speed-dependent); `PiCameraSource` sets the sensor's `FrameRate` control so the ISP
  never processes the skipped frames. `SAMPLE_FPS=0` disables the cap. `_frame_stride` is a
  pure helper (`tests/test_pipeline_sources.py`); the camera grab-skip loop is cv2-gated in
  the same file (synthetic clip, no MediaPipe).
- **`face_detector_stage.py`** (`FaceDetectorCropStage`, BlazeFace) — `VIDEO` running mode and
  `detect_for_video(mp_image, timestamp_ms)` with a monotonically increasing timestamp, matching
  the notebooks' live-simulation pattern (not MediaPipe's `LIVE_STREAM` callback API, a
  different shape never validated in any notebook). When more than one face is found,
  `FaceLock` picks the driver's (see "One face" in "Current status"). It publishes
  `face_bbox_raw` (the raw box `PartsInferenceStage` builds the square training crop from),
  `face_bbox` (margin-expanded, for the overlay), `face_lock` (`{faces, ignored, switch_s,
  takeover_s, switched, reacquired}`), `face_switched` on a switch or take-over frame, and
  `timing_ms["face"]`.
- **`parts_stages.py`** — `PartsInferenceStage`: crop → detect → remap → track → rules in one
  stage, writing `ctx.detection`, `ctx.features["parts"]` and `ctx.features["timing_ms"]`.
  Frames without a face still tick the rules ("no face for 5 s" is a rule). On a
  `face_switched` frame it runs the rules first, then `reset_signals()` and a tracker reset.
- **`output_stages.py`** — `LoggingOutputStage` (one readable line per frame: severity, reason,
  face-lock tokens, eye/mouth/head states, timers, timings) and `MetricsLogOutputStage`
  (`OUTPUTS=metrics`, one JSON line per frame to `METRICS_LOG_PATH`, with the `face_lock` key).
  Both are built on `parts_frame_record(ctx)`.
- **`mjpeg_output_stage.py`** — `MjpegStreamOutputStage`, a demo-only sink: draws
  `pipeline/parts_overlay.py`'s `draw_parts_overlay()` (part boxes with track IDs, the severity
  banner, timer bars, the thresholds/precision/ms footer), falling back to
  `draw_detection_overlay()` if a frame has no parts features, onto each frame and serves it as an MJPEG-over-HTTP stream (stdlib `http.server` only,
  no new pip dependency) — viewable from any device's browser on the network, with no display
  attached to whatever machine runs the pipeline and no need for non-headless OpenCV/X11 in the
  container. **No authentication** — see "Demo" above for why this defaults off (`OUTPUTS`) and
  isn't for a real deployment. `draw_detection_overlay()` is a standalone function, not a
  method, specifically so a future stage (e.g. "save the annotated demo to a video file") can
  reuse it without the HTTP-serving machinery — this was the "future video-overlay output
  stage" extension point mentioned in earlier notes here; it's now built, not just planned.
  `FaceDetectorCropStage` populates `ctx.features["face_bbox"]` so this stage's box-drawing has
  something to work with.
- **`downloader.py`** — `download_face_detector_bundle()`, called from `src/main.py`'s pipeline
  builder as well as at Docker build time — see "Model download strategy" above.
- **`latency.py`** — `StageStats`, the per-stage diagnostics every `Stage` owns
  (`self.stats`). Each stage's own thread feeds it, per frame: `proc` (`process_item()` time —
  on a source, the `cap.read()`/`capture_array()` grab, timed inside `sources.py`); `wait`
  (queue time before the stage, from `FrameContext.enqueued_at` stamped by `Stage._emit`);
  `inq` (input-queue depth, `qsize()` sampled once per consumer loop); drop-oldest drop
  counts; optional named **phases** (`record_phase()` — `PartsInferenceStage` records `crop`,
  `infer`, `decode` and `rules`, so the model's own time is separate from the work around it). Sinks also feed `e2e` (`time.monotonic() - FrameContext.created_at`,
  the whole capture-to-output trip). Every `LATENCY_LOG_INTERVAL` seconds (env, default 10;
  `0` disables — `main.py` reads it, `Pipeline.__init__(..., stats_interval=)` distributes it
  to every stage before threads start) it logs one
  `stats <stage>.<role>: wait(...) proc(...) <phases...> inq(...) e2e(...) drop=... life(...)`
  line under the `cv_argus.pipeline.stats` logger. Full reading guide (highest `proc` = compute
  bottleneck; highest `wait` = where frames stack up) is in `latency.py`'s module docstring and
  the README's "Finding the bottleneck". Stdlib only, so it stays on the
  no-`cv2`/`mediapipe`/`tf` import path. Not thread-safe by design — one instance per stage,
  one writer thread. This is instrumentation only: it does **not** cap the frame rate or drop
  frames itself (the 5 fps cap lives in `sources.py`, above).

`pipeline/__init__.py` re-exports all of the above, but lazily for anything needing `cv2`/
`mediapipe`/`tensorflow` (everything except `stage.py`, `latency.py`, `downloader.py`, and
`output_stages.py`) — see that file's module docstring for why: it keeps `Stage`/`Pipeline`/
`FrameContext` importable and testable without the full stack installed.

`src/main.py` wires all of this together: `SOURCE` picks where frames come from
(`video_capture` default / `picamera`), and `OUTPUTS` picks one or more sinks (comma-separated
`logging` / `metrics` / `mjpeg`, `logging` default) — connected to the same inference stage via
repeated `Stage.connect()` calls (fan-out), not a new mechanism. The chain is always source →
BlazeFace → `PartsInferenceStage` → outputs + bridge; there is no `PIPELINE` switch any more.
Runs until a signal arrives or the source (e.g. a video file) ends on its own — see
`Pipeline.is_alive()` in `stage.py`.

### `orchestrator/`, `buffer/`, `sender/`, `alerts/` — done (Bluetooth protocol v2)

The exact rules live in `docs/designs/final-tweaks-contracts.md`, section C1 (C1.4 record JSON,
C1.5 events, C1.6 buffer). Where that file and this text differ, the contract wins.

Built as **three independent peer components** (Pipeline, `Orchestrator`, `SenderServer`),
coupled only through `buffer/`'s SQLite file — not as more pipeline stages, and not as one
component owning the others' lifecycles. `main.py`'s `main()` builds and starts/stops all
three; see its module docstring for the full picture and the shutdown-order rationale.

Data flow: the pipeline's `PartsInferenceStage` → `OrchestratorBridgeOutputStage` (a thin,
always-on sink — *not* one of `OUTPUTS=`'s demo/observability toggles) → `Orchestrator`'s own
decision-loop thread → `alerts/`'s `Alert.new()` → `buffer/`'s `Buffer.enqueue()` (state
`outbox`) → (later, asynchronously) `sender/`'s `SenderServer` answers the ESP32's next Bluetooth
session: it serves outbox rows, and applies what the ESP32 reports back (`ACK`, `FAIL`, `TAKEN`).

**`alerts/` — the shared data model.** One envelope dataclass, not one class per alert type
(the draw.io diagram shows a single "Alert Model" box feeding a single "Queue Message Local
Buffer (SQLite)" box). `models.py`'s `Alert` carries:
- `id` (uuid4 hex; the backend dedups by it as `client_event_id`), `kind`, `level` (2 `Drowsy`,
  1 `Not Drowsy`, `None` for `No Face`), `source_id`, a flexible `payload` dict and
  `geolocation` (**always `None`**, see below);
- `created_at_ms` (**wall clock**, `time.time()`), `mono_ms` (`time.monotonic()`), `boot_id`
  and `clock_synced`, so the age of a row can be computed later without trusting the Pi's clock.

`AlertKind` has only `DROWSINESS` in v2: the `route_status` heartbeat and its `route_status()`
factory are gone (contract C1.4). `serialization.py`'s `to_json()`/`from_json()` are compact,
newline-free and strict (a non-finite number becomes `null`); an unknown `kind` raises a clear
`ValueError`.

**`age_s`** (contract C1.4) is computed **at serve time, never stored**, by
`alerts.serialization.age_seconds()`, and added to every served record line. It's the seconds
from the event to the moment the line is written, rounded to 0.1 s:
- same boot and a `mono_ms`: monotonic difference (immune to NTP steps and an unsynced clock);
- else, when both the row's and the Pi's wall clock are synced: wall-clock difference, clamped
  to >= 0;
- else `null`: the age is unknowable, the ESP32 answers `FAIL <id> too_old -`, and the row is
  archived.
The ESP32 never sees absolute times; the backend turns `age_s` into a timestamp with its own
clock. `src/tests/edge_chain` patches `serialization.time`, `models.time` and
`orchestrator.time` to drive these clocks, so moving `age_seconds` or changing its time source
breaks it: tell the lead first.

**Geolocation is never populated by this module.** The draw.io diagram wires the geolocation
module directly to the ESP32, not the Pi — this device has no GPS. The ESP32 adds its own GPS
position (and keeps the event-time position in the body it composes) when it relays an event to
the backend; nothing travels the other way.

**`orchestrator/` — one decision loop, not several timers.** `bridge.py`'s
`OrchestratorBridgeOutputStage` has zero decision logic — it only pushes `(source_id,
detection)` onto a plain `queue.Queue` that `Orchestrator` owns, via `put_nowait`/drop-on-full
(a dropped detection just costs one sample, never blocks the pipeline). `orchestrator.py`'s
`Orchestrator` runs a single `while` loop on its own thread, gated by
`queue.get(timeout=ORCHESTRATOR_LOOP_POLL_SECONDS)`. Every wake — a detection or a timeout —
checks for a class or severity change, for a due keepalive and for periodic buffer maintenance.

It reports **readings, not sparse alerts**; the ESP32 decides which are backend events (C1.5,
see "Contract with the ESP32" above):
- **Severity changes** are emitted at once as `transition: true`, with `previous_severity` and
  the `episode` id. They aren't debounced again (the rules have their own hold and recovery),
  and a severity change adopts this frame's class at once, so the recovery record says
  `Not Drowsy` right away. A reason change at the same severity emits nothing.
- **Class changes** (`Drowsy` / `Not Drowsy` / `No Face`) are debounced in both directions by
  `ORCHESTRATOR_DEBOUNCE_FRAMES` (3, about 0.6 s at 5 fps). The first
  `ORCHESTRATOR_WARMUP_FRAMES` (10) are ignored.
- **Keepalives** every `ORCHESTRATOR_KEEPALIVE_SECONDS` (2 s) while nothing changes
  (`transition: false`, `previous_severity` = `severity`), so a stalled pipeline is visible to
  the ESP32. They never reach the backend.
- **No heartbeat.** `ORCHESTRATOR_HEARTBEAT_INTERVAL_SECONDS` and the 60 s `RouteStatus("OK")`
  are removed (contract C1.5); the ESP32 sends GPS every 5 s on its own.
- `DetectionResult.extras` (the rules' severity, reason, timers) is merged into the payload
  *before* the contract keys (`class_name`, `probabilities`, `transition`), so it can never
  overwrite them.
- Every `BUFFER_MAINTENANCE_INTERVAL_SECONDS` (600 s) the loop calls `prune_sent` (sent rows
  older than `BUFFER_KEEP_SENT_SECONDS`, a day) and `cap_unsent` (below).

State is per camera `source_id`. `start()`/`stop()`/`join()`/`is_alive` mirror `Pipeline`'s
method names (not by inheritance) so `main.py` treats all three components alike.
`fusion_contract.py` is the typed reference for the ESP32's drowsy+grip fusion (ported to
`fusion.cpp`); it isn't wired into `main.py`.

**`buffer/` — the SQLite queue.** `store.py`'s `Buffer` opens with `PRAGMA journal_mode=WAL` +
`synchronous=NORMAL` + `busy_timeout=5000`, and wraps every write in one `threading.Lock`: WAL
lets readers run without blocking on a writer, but doesn't make two Python threads' *writes*
atomic with each other. One `Buffer` is built in `main.py` and shared by `Orchestrator` and
`SenderServer` — this is the project's actual "concurrencia real" argument (thesis draft,
criterion 3.1.1), so the two threads genuinely share the same lock. `enqueue()` is idempotent
(`INSERT OR IGNORE` on `id`). `BUFFER_DIR`/`BUFFER_DB_FILENAME` set the file
(`constants.BUFFER_DIR_DEFAULT`/`BUFFER_DB_FILENAME_DEFAULT`).

*Row states* in table `alerts` (user decision 2026-10-20, "live first, dead-letter on failure"):
- `outbox`: just produced. Written to disk at once so a reboot can't lose it, but this is the
  live path: rows normally leave it within seconds.
- `dlq`: the dead-letter queue. It holds **critical event rows only** (`kind='drowsiness'`,
  `transition` true, severity `critical`, previous severity not `critical`, missing counting as
  `none`).
- `sent`: delivered, or nothing to deliver (a keepalive, a non-event). Pruned after a day.

*The `TAKEN` lease.* The ESP32's sender task holds the events it took and reports each later.
`lease()` sets `lease_until_ms = now + BUFFER_LEASE_SECONDS` (120 s) on rows served **in the same
session**. A leased row is skipped by `fetch_unsent`, `fetch_dlq`/`serve_dlq`, `expire_outbox`,
`cap_unsent` and the waiting counts, so it isn't served twice, expired or capped while in flight.
When the lease lapses the row becomes visible again in the state it had; that is not an attempt.
`ACK` and `FAIL` clear the lease.

*Live outbox expiry* (`expire_outbox`, run by `SenderServer`'s housekeeping every 5 s, so **only
while a sender runs**): a non-leased outbox row older than `DEFAULT_OUTBOX_MAX_AGE_SECONDS`
(60 s, by enqueue time) means the ESP32 isn't picking up. A critical event row moves to the DLQ
as `esp32_unreachable` (no body, `attempts` stays 0); any other event (a medium, a driver OK) is
archived as `esp32_unreachable`; non-events (keepalives) are deleted. With
`SENDER_TRANSPORT=none` nothing expires; only `cap_unsent` bounds the outbox.

*`FAIL <id> <reason> <body>`* (`apply_fail`, for a row in `outbox` or `dlq`):
1. not a critical event row → archived at once with `<reason>` (normally `retry_window`: a
   medium/OK whose live window ran out);
2. a critical, reason `preempted` (a DLQ replay a live event pre-empted) → stays where it was
   (an outbox row stays in the outbox), lease cleared, `attempts` and `dlq_round` unchanged;
3. a critical, reason in `BUFFER_ARCHIVE_NOW_REASONS` (`route_finished`, `no_route`, `too_old`,
   `wrong_truck`, `invalid`) → archived at once;
4. any other reason (`no_network`, `timeout`, `http_503`, `retry_window`, `bad_response`, ...) →
   `attempts += 1`; an outbox row moves to the DLQ in the current round; `dlq_error` = reason; the
   body is stored only if none was stored yet (the first composed event, with the event-time GPS
   position, wins). At `BUFFER_DLQ_MAX_ATTEMPTS` (5) the row is archived as `max_attempts`.
The live failure that dead-letters a critical is attempt 1; each failed replay is one more.

*Repeated-FAIL guard* (C1.6 `[sync 2]`). Rule 4 applies only to an `outbox` row (leased or not)
or to a **leased** `dlq` row. A rule-4 FAIL for a non-leased dead letter is a resend of one
already applied (every FAIL clears the lease): it's ignored, not counted in `failed`, and logged
at debug. So the ESP32 resending outcomes it got no `OK` for can never count an attempt twice.
Accepted cost: a replay whose 120 s lease lapsed before its FAIL arrived is an uncounted attempt.
Rules 1-3 need no guard. **`[sync 3]` body fill:** an ignored FAIL still sets `dlq_body =
COALESCE(dlq_body, <body>)` when the body is a JSON object, and changes nothing else (no attempt,
no `dlq_error`, not counted). The case: a live critical the ESP32 had `TAKEN`, whose lease lapsed
during a Bluetooth outage, so `expire_outbox` dead-lettered it as `esp32_unreachable` with no
body; its late dead-lettering FAIL must not lose the event-time GPS position.

*DLQ order: newest first, round-robin, one row per session.* `serve_dlq(limit=1)` (what a `DLQ`
session calls) returns the non-leased dead letter with the smallest `dlq_round`, newest first
(`rowid DESC`) on ties, and bumps its `dlq_round` at serve time, whether or not it's then TAKEN.
A row entering the DLQ gets the current minimum round, so it goes out ahead of older rows but
can't take several attempts in a row. `fetch_dlq()` is the read-only listing in the same order
(tests, `buffer.status`); it never changes the order.

*Archive.* Everything given up on moves to the `archive` table: every column of `alerts` plus
`archived_at_ms` and `archive_reason`, as `INSERT` + `DELETE` in one transaction under the write
lock. Archived rows are **never deleted, never served, never resent**. Reasons: the FAIL reason
(rules 1 and 3), `max_attempts`, `esp32_unreachable`, `cap_unsent`, `migrated_v2`.

*`cap_unsent`* (`BUFFER_MAX_UNSENT_ROWS` = 20000): over the cap, keepalives are deleted first,
then the oldest remaining rows are **archived** as `cap_unsent` (no longer deleted). **Leased rows
are spared** (neither deleted nor archived; they still count towards the total): the ESP32 may
already have delivered them.

*Migration on open* (`_init_schema` → `_migrate_v2`, idempotent): adds the `archive` table and
the new columns (`lease_until_ms`, `dlq_round`, `last_error`, ...); moves every unsent
`route_status` row and every non-critical dead letter to the archive as `migrated_v2`; archives a
remaining dead letter already at 5 attempts as `max_attempts`. Before that, a file from before
the dead-letter queue (no `state` yet) gets its unsent rows as `outbox`, its sent rows as `sent`.

*`python -m cv_argus.buffer.status [--limit N] [--bodies]`* (read-only, never serves so it never
changes the replay order): the outbox, the DLQ (attempts, round, lease, error, optionally the
composed body) and the archive (count by reason plus the newest rows).

**`sender/` — a custom Bluetooth SPP server; the ESP32 is the client.** `protocol.py` is a pure
(no I/O) grammar; the ESP32 opens a fresh connection per session, one session at a time, and its
first line decides the session:
```
C: CONFIG                       S: CONFIG {json} | NOCONFIG
C: PULL <n>                     live pull, n clamped to MAX_PULL_LIMIT (50); the ESP32 asks 8
S: <record JSON line> x0..n     non-leased outbox rows, oldest first, each with age_s
S: END <dlq_waiting> <outbox_waiting>
C: ACK <ids> | FAIL <id> <reason> <body> | TAKEN <ids>   x0..m, any order
C: DONE
S: OK acked=<a> failed=<f> leased=<l> dlq=<d> outbox=<o>
C: DLQ <k>                      dead-letter pull, k clamped to MAX_DLQ_LIMIT (1)
S: <record JSON line with "dlq": {error, body, failed_at_ms, attempts, queued_s}> x0..1
S: END ... / report lines / DONE / OK ...  as above
C: ACK <ids> | FAIL ...         report session: outcomes for rows taken earlier
C: <report lines> / DONE        S: OK ...
```
- Replies: `ACK` → `ACKED <n>` (rows actually marked sent), `TAKEN` → `LEASED <n>` (only ids
  served in **this** session count), `FAIL` → no reply. `ACK`/`FAIL` are honoured for any id in
  `outbox` or `dlq`, leased or not; an id that's unknown, sent or archived is ignored.
- **Report lines take effect the moment they're read** (`ACKED`/`LEASED` are sent at once).
  `DONE` → `OK` is only a summary. A session that breaks before `OK` keeps every line already
  applied, and the ESP32 may resend outcomes it has no `OK` for: safe, because ACK is
  idempotent and a repeated FAIL is ignored (the guard above).
- `END`/`OK` counts are rows not leased; `END` is always sent, even with 0 rows. The Pi waits up
  to 30 s for the report, 1 s at a time; a disconnect or timeout leaves every unreported row where
  it was. A served row the ESP32 neither ACKs, FAILs nor TAKEs is served again.
- **Critical invariant:** `buffer.mark_sent()` is called **only** on an `ACK` line. A connection
  dropped after `END` changes nothing, so the next pull re-serves the rows: a duplicate delivery
  is possible (the backend dedups by `client_event_id` = the row id), a lost one is not.
- What the ESP32 does (contract C1.8, T3's side, summarised): every `PULL` record updates its
  latest severity; a non-event is ACKed in the same session; an event is `TAKEN` and queued for
  its sender task, which posts it to the backend's report endpoint and later sends `ACK` or
  `FAIL <id> <reason> <body>` (the composed event as `<body>` for the FAIL that first
  dead-letters a critical). It asks `DLQ 1` only when `dlq_waiting > 0` and no live event is
  pending; a replay carries `delivery: "dlq"` and `queued_seconds` to the backend.
- `server.py`'s `SenderServer` runs a single-session-at-a-time accept loop (one Pi, one ESP32)
  against a `Transport`/`Listener` pair (`transport.py`). Its housekeeping (every 5 s, between
  sessions) runs `expire_outbox` and logs the queue. Logs: every FAIL as it happens, one line
  per session that delivered, took or failed an event, the queue every 30 s while anything waits,
  `ESP32 <mac> connected` / `link down?` transitions, and each change in the severity the ESP32
  acked.
- Two commands in the running container: `python -m cv_argus.sender.inject <severity> [--episode
  ID]` queues a fake reading (tests the link without the camera; only a critical can end up in
  the DLQ), and `python -m cv_argus.buffer.status` (above).
- `bluetooth_transport.py`'s `BluetoothSppTransport`/`BluetoothSppListener` use stdlib
  `socket.AF_BLUETOOTH`/`BTPROTO_RFCOMM` (no PyBluez). `AF_BLUETOOTH` is referenced only inside
  method bodies, so *importing* the module never needs Bluetooth support; only *constructing* a
  listener can fail, with a clear `OSError`. `sender/__init__.py` re-exports both lazily (the
  same `_LAZY` `__getattr__` pattern as `pipeline/__init__.py`). `transport.py`'s
  `FakeTransport`/`FakeListener` (an in-memory, queue-backed duplex pair) are what every
  `sender/` test runs against — no real socket in the unit suite.

**Device credentials — `CONFIG`.** This module never calls the backend, but it *holds* the
truck's backend credentials: `sender/device_config.py`'s `DeviceConfig` (truck id, device API
key, backend URL), read from `ARGUS_*` env vars that `docker-compose.yml` loads from the
dashboard-issued `argus-device.env`. A session may be a single `CONFIG` request instead of
`PULL`/`ACK`; `SenderServer` answers `CONFIG {json}` or `NOCONFIG`. The point is that the Pi is
the provisioned, re-deployable half of the truck, so the ESP32 firmware stays generic (no
per-truck key flashed in). The same reply also carries the ESP32's network settings — WiFi
SSID/password and the SIM's APN (`ARGUS_WIFI_*`/`ARGUS_GPRS_*`, optional, from `.env` rather
than `argus-device.env` so a key rotation doesn't wipe them, and never `.strip()`ped since an
SSID may end in a space) — so the firmware carries no network config either; no APN means the
ESP32 stays on WiFi. Because that reply carries secrets, the link must be paired + encrypted
(`BLUETOOTH_SECURITY`, default `medium`): `BluetoothSppListener` sets `BT_SECURITY` on its
listening socket, and `BluezProfileListener` registers its profile with
`RequireAuthentication`. `ESP32_BT_MAC` allowlists the peer. The key is never logged and
`repr(DeviceConfig)` masks it. A partial `ARGUS_*` set is a `SystemExit` at startup.
`tests/test_sender_device_config.py` covers all of it over fakes (including the `setsockopt`
call); the real RFCOMM security handshake is unverified until real Pi + ESP32 hardware runs it.

**`bluez_profile.py` — how the Pi actually serves the ESP32 (`SENDER_TRANSPORT=bluez`).** Linux
creates Bluetooth sockets only in the host's own network namespace (`EAFNOSUPPORT` anywhere else,
checked in a container), and this Pi runs rootless Docker, where even `network_mode: host` is a
separate namespace. So `BluezProfileListener` doesn't open a socket at all. Over the host's D-Bus
socket (mounted by `docker-compose.pi.yml`) it:
- powers the adapter on and makes it pairable (`PairableTimeout 0`);
- registers a `NoInputNoOutput` `Agent1` that accepts only `ESP32_BT_MAC` (rejects and logs any
  other MAC, and every PIN/passkey request), and marks an accepted ESP32 trusted;
- registers a `Profile1` for the Serial Port UUID on `BLUETOOTH_CHANNEL`, so bluetoothd listens,
  advertises it over SDP, enforces `RequireAuthentication`, and passes each accepted socket in
  with `NewConnection` (an fd over D-Bus works across namespaces). It's wrapped in the same
  `BluetoothSppTransport`, so `SenderServer` is unchanged.
It authenticates with `AuthExternal(uid=UID_NOT_SPECIFIED)`: dbus-daemon takes our identity from
the socket credentials, because the container's uid 0 is a different uid on the host. A
restarted bluetoothd (`NameOwnerChanged` for `org.bluez`) or a lost bus re-registers everything
with a backoff. `tests/test_sender_bluez_profile.py` runs it against a private `dbus-daemon` with
a stand-in BlueZ: setup calls, agent accept/reject, trust, a real fd through `NewConnection`, a
restart, close, and "no BlueZ" failing construction. On real hardware, the Pi + ESP32 paired
through it and answered `CONFIG` on 2026-10-20 (under protocol v1).

This satisfies the grading-criteria constraint behind all of it (3.2's "not a pre-built
client/server service" note): the PULL/DLQ/report grammar is a protocol this project defines and
implements itself on both ends (this module's Pi side, `src/esp32-argus`'s client), not a call
into someone else's client/server library.

## Open decisions that affect this module later

- **Pi ↔ ESP32 transport: decided AND implemented as Bluetooth SPP, with the ESP32 as the
  polling side** — `sender/`'s `SenderServer` (see "`orchestrator/`, `buffer/`, `sender/`,
  `alerts/` — done" above) is the server; the ESP32 periodically connects and pulls unsent
  records, never the reverse. What's still genuinely open, not just undocumented:
  - **Protocol v2 hasn't run on real hardware.** The v1 link paired and served `CONFIG` on the
    real Pi + ESP32 (2026-10-20), and the ESP32's HIL tests passed over WiFi against a bench
    backend under v1. v2 (TAKEN lease, one-row DLQ, report sessions, the archive) is tested here
    only against `FakeTransport`, and end to end only in `src/tests/edge_chain` (a Python mirror
    of the ESP32 client, run at integration). v2 is **not compatible** with v1: the Pi
    image and the ESP32 firmware must be deployed together. Don't describe the v2 link as
    validated on hardware.
  - **Container access: decided as the host's BlueZ over D-Bus** (`bluez_profile.py`,
    `SENDER_TRANSPORT=bluez`, set by `docker-compose.pi.yml` with the host's
    `/run/dbus/system_bus_socket` mounted), **not** a raw socket, `/dev/rfcommN` or
    `network_mode: host`. None of those can work under the Pi's rootless Docker: a Bluetooth
    socket only exists in the host's network namespace (see the `bluez_profile.py` paragraph
    above). `docker-compose.yml` (laptops) stays `none`: there's no ESP32 to serve, and a missing
    adapter would stop startup. Pairing is automatic (no `bluetoothctl`) and limited to
    `ESP32_BT_MAC`.
- Real camera on the Pi: settled as `picamera2` (see "Python packaging" above) for the CSI
  module. **Verified at the camera level on a real Pi 5** (Raspberry Pi OS / Debian 13, IMX219,
  rootless Docker): picamera2 inside this image lists the sensor and captures frames. The full
  cv-argus pipeline on that Pi hasn't been run end to end yet. Four things that took real
  debugging, all now encoded in the repo — don't undo them:
  - **picamera2 + libcamera come from Raspberry Pi's apt repo (bookworm suite), not pip.** The
    `libcamera` Python bindings aren't on PyPI (pip `picamera2` fails with `No module named
    'libcamera'`) or in Debian's own repo. The Dockerfile adds the RPi repo on arm64 and appends
    `/usr/lib/python3/dist-packages` to `sys.path` via a `.pth` (after site-packages, so pip's
    numpy/TF win). Bookworm, matching the image's Python 3.11 — not the host's OS release.
  - **Device list** (`docker-compose.pi.yml`): `/dev/media0-3`, `/dev/video0-7` (rp1-cfe),
    `/dev/v4l-subdev0-2` (sensor/CSI-2 — missing these makes libcamera skip the camera),
    `/dev/video19-35` (PiSP backend), `/dev/dma_heap/*`.
  - **`/run/udev:/run/udev:ro`** — without udev's database libcamera can't group the nodes
    ("Unable to acquire a CFE instance") and lists zero cameras.
  - **Rootless Docker + dma-heap ACL**: the container can't use the host's `video` group, and
    `/dev/dma_heap/*` (root:video 0660) has no per-user ACL, so allocation fails ("Could not open
    any dma-buf provider"). `scripts/pi-host-setup.sh` installs a udev rule granting the Docker
    user an ACL on the camera/media/dma-heap nodes at every boot (a headless truck boot can't rely
    on logind's desktop-seat ACLs).
  - Also: picamera2's `"RGB888"` yields **BGR** arrays (DRM naming) — `PiCameraSource` asks for
    `RGB888` to get BGR; it used to ask for `BGR888`, which silently swapped red/blue.
  A dev laptop's USB webcam keeps using plain `cv2.VideoCapture` (`SOURCE=video_capture`).

## `scripts/` — dataset-prep utilities, not part of the deployed package

Not imported by `cv_argus` at runtime — these are standalone scripts you run locally, by hand,
before uploading anything to Drive.

> The rest of dataset creation (the notebook `01`/`02`/`06`/`09` stages) has been reimplemented
> as local CPU-parallel scripts under **`src/dataset/`** — Colab was unreliable for the
> multi-hour extraction runs. That pipeline reads `src/dataset/raw/raw_videos/` directly and
> expects clips already labelled `level_1` / `level_2` (no relabel step). See
> `src/dataset/README.md`. `extract_uta_rldd_clips.py` here still emits 3-class `level_<1-3>`
> clips (UTA-RLDD's native scheme) — collapse them to binary before feeding the local builds.
> It also grew a `KeyboardInterrupt` cleanup so a Ctrl-C mid-encode doesn't leave a partial clip.

`extract_uta_rldd_clips.py`: reads a
downloaded [UTA-RLDD](https://sites.google.com/view/utarldd/home) zip archive (e.g.
`Fold1_part1.zip`) directly (never extracts the whole ~13GB archive at once — pulls one
~10-minute source video to a temp file, processes it, discards it, moves on), cuts a few short
random non-overlapping sub-clips per source video via `ffmpeg`/`ffprobe`, and writes them as
`level_<1-3>_clip_<N>.mp4` into new `subject_<N>` folders under `scripts/output/` — gitignored,
since it's large binary video output, not something to commit. Numbering continues after
`notebook/01_dataset_creation_lstm.ipynb`'s existing `subject_01`..`subject_06` and is resumable
across runs, not something requiring a manually-tracked `--start-subject` each time: a
`subject_assignments.json` written into the output folder persists which (zip, participant) pair
got which subject number, so re-running against the same or additional zips reuses existing
numbers and auto-continues numbering for newly-seen ones — `--start-subject` is now only an
explicit override, not a required argument. See the root `CLAUDE.md`'s "01_dataset_creation_lstm.
ipynb" section (first bullet) for the current raw dataset ingestion status — as of the last run,
`subject_01`–`subject_54` (54 subjects, 48 of them external) are extracted, with two known
intentionally-incomplete subjects (`subject_38`, `subject_52`) flagged there rather than fixed.

Deliberately reuses the `level_` filename prefix rather than a separate `class_` one (1=Alert,
2=Low Vigilant, 3=Drowsy — the *final* class, not a sub-graded 1-6 value UTA-RLDD doesn't
provide), which means the notebook's metadata-generation loop has to disambiguate by **subject
number**, not filename, when deciding whether to apply the original 1-6→3-class mapping — see
`01_dataset_creation_lstm.ipynb`'s `EXTERNAL_SUBJECT_START` constant, and keep it in sync with
`--start-subject` if that's ever run non-default.

## Module status at a glance

| Module | Responsibility |
|---|---|
| `model/` | **done** — tiny YOLO parts detector (int8/float), crop geometry, IoU tracker, gdown download |
| `pipeline/` | **done** — threaded Stage/Pipeline abstraction, BlazeFace stage, `PartsInferenceStage`, logging/metrics/MJPEG sinks; wired into `src/main.py` |
| `alerts/` | **done** — `Alert`/`AlertKind` data model (only `drowsiness` in v2) + serialization + the serve-time `age_s`, no logic/I/O |
| `orchestrator/` | **done** — bridge stage + decision-loop thread (immediate severity changes, debounced class readings, keepalives, no heartbeat), plus `parts_rules.py` (the severity rule engine, `reset_signals()`); not yet run against a real multi-hour drive |
| `buffer/` | **done** — WAL-mode SQLite: outbox / critical-only round-robin DLQ / sent, the `TAKEN` lease, FAIL rules with the repeated-FAIL guard, the `archive` table + v2 migration, `buffer.status`; `tmp_path`-tested including a real two-thread concurrency smoke test |
| `sender/` | **done** as a Pi-side implementation — custom Bluetooth SPP protocol v2 (PULL / DLQ 1 / report sessions), unit-tested against `FakeTransport`; v2 **not** run against real ESP32 hardware |

What's otherwise in place: the container (Dockerfile, docker-compose.yml, two volumes for the
model cache and the SQLite buffer — both artifacts baked in at build time, see "Model download
strategy"), the packaging (`setup.py` mapping `src/` to the `cv_argus` import name, all six
subpackages registered in `packages=[...]`), and a real entry point (`src/main.py` +
`src/__main__.py`, run via `python -m cv_argus`, `python -m cv_argus.main`, or the
`cv-argus-run` console script) that builds and starts all three top-level components (Pipeline,
`Orchestrator`, `SenderServer`) together — see `main.py`'s module docstring. **Real, stated
limitations, not glossed over**: protocol v2 hasn't run between the real Pi and ESP32 (only
v1's pairing and `CONFIG` did, see "Open decisions"); and the debounce/keepalive/lease/attempt
constants in `constants.py` are reasoned-through defaults, not tuned against a real recorded
drive.

## Tests

`tests/` is a `pytest` suite mirroring `src/` (`test_model_*`, `test_pipeline_*`,
`test_alerts_*`, `test_buffer_*`, `test_orchestrator_*`, `test_sender_*`, `test_main.py`,
`test_constants.py`) — roughly 600 tests once the final-tweaks branches are merged (the face
lock and eye-box filter added about 55 of them); `pytest` prints the current count. One skips
when `src/esp32-argus/test/vectors/event_vectors.json` isn't in the checkout, and the Docker
build test is deselected by default (below). Run it with `pytest` from
`src/cv-argus/` after `pip install -e .`. Config lives in `pyproject.toml`'s
`[tool.pytest.ini_options]`. **`pytest` itself is deliberately not in `requirements.txt`** —
that file is the exact, pinned dependency list this image ships to the Pi, and a test runner has
no business being part of it; install it separately (`pip install pytest`) for local runs.

- **`docker build`/`docker compose build` now run this same hermetic suite automatically, as a
  build gate** — `Dockerfile` is multi-stage: a `test` stage (`pip install pytest` scoped to
  just this stage, `COPY tests`, `RUN pytest`) sits right after the package becomes importable
  (`pip install -e .`) but *before* the expensive part of the build, the ~1GB model-artifact
  download from Drive — a broken test fails fast, without paying for that download first.
  `runtime` is `FROM base`, not `FROM test`, and pulls back only `/app/src` via `COPY
  --from=test /app/src ./src` — content-wise a no-op (identical to what `base` already has;
  `test` only additionally copied `tests/` alongside it), but it forces Docker to build and pass
  `test` before `runtime` can be built at all, without `tests/` itself ever reaching the
  deployed image. Verified both directions for real: a clean build runs the whole suite then
  continues into the real model download and produces a working image; a deliberately-broken
  test failed `docker build` outright, before the Drive download ever started. Also verified the
  **full** build (real Drive downloads) still succeeds end to end after this restructuring, not
  just the fast `--target test` path.
- **`.dockerignore` had a real, unrelated bug this surfaced**: `scripts/output/` (this module's
  local dataset-extraction output, gitignored, can reach double-digit GB) wasn't excluded, even
  though the Dockerfile never `COPY`s `scripts/` in at all — every `docker build` was sending a
  **16.43GB** build context to the daemon for nothing. Fixed by excluding `scripts/output/`;
  context dropped to ~1MB. Also removed `tests/` from `.dockerignore` — it used to be excluded
  on purpose (nothing copied it in, and it kept test edits from busting the layer cache); now
  that the `test` stage above needs it in the build context, editing a test file busting the
  cache from that `COPY` onward is the intended behavior, not a regression.
- **The default run is hermetic** — no network, no camera, no Google Drive, no model
  download. That's a hard rule, not an aspiration: every `model/downloader.py` /
  `pipeline/downloader.py` test either exercises the skip-if-cached branch or monkeypatches
  `gdown` / `urllib.request.urlretrieve`. Keep it that way — a test that would fetch something
  belongs behind the `docker` marker (or nowhere).
- **The parts detector is tested with stub backends and ideal grids** (`test_model_parts_
  detector.py`). That covers decode/NMS against the notebook's perfect-network check, the pair
  decision thresholds and the env overrides. The only real model is a tiny Keras net exported
  to TFLite in-test; the real Drive files are never loaded in the unit suite.
  `test_model_parts_tracker.py`, `test_orchestrator_parts_rules.py` (a fake 5 fps clock),
  `test_pipeline_parts_stages.py` (crop → rules → overlay on a synthetic frame) and
  `test_pipeline_parts_logging.py` (timings, log line, JSONL sink, the summarizer) cover the
  rest. `test_model_parts_fallback.py` runs the probe in **real child processes** against tiny
  in-test models. That covers a working model, a corrupt file, the wrong output shape, a native
  crash (`os.abort()`) and a hang, plus both fallback paths.
- The alert pipeline (`test_alerts_*`, `test_buffer_store.py`, `test_orchestrator_*`,
  `test_sender_*`) uses a real SQLite file under `tmp_path`, the in-memory `FakeTransport`/
  `FakeListener` and a duck-typed `DetectionResult`. `test_sender_dlq.py` is the protocol v2
  suite: the C1.5 event rule (Python and SQL, plus T3's shared vectors), FAIL rules 1-4, the
  repeated-FAIL guard and its `[sync 3]` body fill, the lease, the DLQ order, `cap_unsent`,
  expiry, the v1 -> v2 migration, real server sessions (including a session that breaks before
  `OK` and a resent FAIL), the orchestrator's severity transitions, `inject` and
  `buffer.status`.
- `test_pipeline_latency.py` covers `StageStats` — the aggregates/percentiles, the
  interval-gated `maybe_report`, the disabled-interval no-op, `wait`/`e2e`/phase/drop
  recording — with no native deps (it's stdlib-only, like `latency.py` itself).
- `FaceDetectorCropStage` needs the real BlazeFace bundle to construct, so only its pure helper
  (`_expand_and_clip_bbox`) is unit-tested. End-to-end MediaPipe coverage is the opt-in Docker
  tier plus a real-clip run (`scripts/demo.sh --camera src/demo_clips/<clip>.mp4`, then
  `scripts/summarize_metrics.py`). The crop's pixel identity with training is
  `src/dataset/tests/test_parts_crop_equiv.py`.
- **`test_docker_build.py` is opt-in** (`@pytest.mark.docker`): deselected from a plain
  `pytest` (`addopts = -m 'not docker'`), and skipped unless `docker` is on `PATH`,
  `CV_ARGUS_DOCKER_TESTS=1` is set, and the daemon is reachable. It builds the image and checks
  `import cv_argus` works inside it, the four artifacts are baked into `/app/models`, the
  downloaders are a cached no-op on re-run, and the `SOURCE`/`OUTPUTS` guards reject bad
  values. Run it before changing the Dockerfile, the compose files, `constants.py`'s Drive IDs,
  or the downloader modules.
- `scripts/smoke_test_pipeline.py` is kept as a standalone hand-run script; its checks are now
  also in `tests/test_pipeline_stage.py` as the maintained version.
- `tests/test_pipeline_sources.py` covers the frame-rate cap: `_frame_stride` (pure), the
  video-file decimation path (cv2-gated against a synthetic clip), the live-camera grab-skip
  pacing loop (`TestProduceLive` — a fake `cv2.VideoCapture`-shaped object plus a monkeypatched
  `time.monotonic`, so the decimation/uncapped/disconnect/stop-event cases are deterministic
  and need no real camera or real sleeping), and `PiCameraSource`'s `FrameRate` control wiring
  (`TestPiCameraSource` — a fake `picamera2` module injected into `sys.modules`, exercising the
  deferred-import path without the real arm-only package). `tests/test_main.py`'s
  `TestSampleFps`/`TestBuildSource` cover `SAMPLE_FPS` parsing (default, float, `0`, negative
  clamp, malformed fallback) and that the parsed value actually reaches
  `VideoCaptureSource(target_fps=...)`/`PiCameraSource(frame_rate=...)`, not just that the right
  class gets built. `tests/test_bootstrap.py` covers the thread-pinning knobs (`configure()`
  only touches the vars when `CV_ARGUS_NUM_THREADS` is set, and never overwrites an existing
  value).


## Working in this module

- **`import cv_argus.bootstrap` stays the first line of `main.py` and `__main__.py`**, above
  every other import. It sets `OMP_NUM_THREADS` / `TF_NUM_*` / etc. from `CV_ARGUS_NUM_THREADS`,
  and the native libraries read those env vars once at load time — moving the import below
  `import tensorflow` / `import cv2` silently makes the thread pinning a no-op. The Dockerfile's
  build-time `python -m cv_argus.model.downloader` bypasses `main.py` and so skips bootstrap;
  that's fine (it only downloads).

- Before touching `model/`, re-read "`model/` — done" and the training-fidelity list in "Current
  status". `decode_grid`/`nms_per_class` must stay notebook 12's code, and the crop must stay
  `detection_dataset.py`'s. Put new behaviour after them (like `apply_pair_thresholds`), not
  inside them.
- Don't add a `mediapipe` import to anything under `model/`. That boundary is deliberate (see
  the `model/` section) — `pipeline/` owns translating MediaPipe results into plain numbers.
- Don't bring the CNN+LSTM pipeline back piecemeal. It lives whole on `archive/cnn-lstm-fused`;
  if it's ever needed again, restore it from there as a unit.
- Don't move CAN-bus/alarm/geolocation logic into this module — that's the ESP32's job per the
  root `CLAUDE.md`. This module only decides and queues; `sender/` is the boundary, not a place
  to reach across it.
- `pipeline/`'s camera loop follows `08_deployment_export_lstm.ipynb`'s "End-to-End Live Stream
  Simulation" cell (`VIDEO` running mode, `detect_for_video` with a monotonically increasing
  wall-clock timestamp) — when adding a new MediaPipe-based stage, keep following that pattern;
  don't switch to MediaPipe's `LIVE_STREAM` callback API, a different shape never validated in
  any notebook.
- When adding a new pipeline stage (a video-overlay output stage, a future model family),
  subclass `Stage`/`SourceStage`/`OutputStage` (`pipeline/stage.py`) rather than writing a new
  ad hoc thread/queue loop — that's the point of the abstraction. If the new stage needs `cv2`/
  `mediapipe`/`tensorflow`, register it in `pipeline/__init__.py`'s `_LAZY` dict rather than
  importing it eagerly, so `Stage`/`Pipeline`/`FrameContext` stay importable without the full
  stack installed (see that file's module docstring).
- `orchestrator/`, `buffer/`, `sender/`, and `alerts/` are done (see their "done" section
  above) — don't reintroduce a simple push-based `Transport.send(alert) -> bool` shape if this
  is ever revisited; the ESP32 is the polling side, confirmed and implemented, not still open.
- **Protocol v2 is a frozen contract** (`docs/designs/final-tweaks-contracts.md`, C1 + C4).
  Changing the grammar, the record JSON, the event rule, the FAIL rules or a C4 value (e.g.
  `BUFFER_LEASE_SECONDS`, `BUFFER_DLQ_MAX_ATTEMPTS`, `MAX_DLQ_LIMIT`) changes the ESP32 firmware
  and `src/tests/edge_chain` too: do it through the contract, not here alone. Don't bring back the
  heartbeat or the no-face LOW/MEDIUM ladder.
- Keep `PartsRuleEngine.reset_signals(self) -> None` and the `>=` in the
  `RULE_NO_FACE_CRITICAL_S` comparison: the face stage calls the first, and the face switch
  timing relies on the second (C1.7).
- Keep `age_s` computed in `alerts.serialization.age_seconds` with `time.monotonic()`/
  `time.time()` looked up through the module's `time`: `src/tests/edge_chain` patches
  `serialization.time`, `models.time` and `orchestrator.time`.
- **Never call `buffer.mark_sent()` anywhere except `SenderServer`'s post-ACK line** — that's
  the one invariant the whole "never lose, duplicates are fine" design rests on (see the
  "done" section's `sender/` paragraph). A tempting-looking shortcut (e.g. marking sent right
  after streaming rows in a `PULL` response, "since the ESP32 basically always gets them") would
  silently reintroduce exactly the alert-loss failure mode this design exists to avoid.
- `docker-compose.yml`'s `SENDER_TRANSPORT` default is `none` (laptops) and
  `docker-compose.pi.yml`'s is `bluez` — see "Open decisions". Don't switch the Pi to
  `bluetooth` or "fix" it with `network_mode: host` / `/dev/rfcommN`: under rootless Docker no
  in-container Bluetooth socket can work. And keep `SENDER_TRANSPORT` out of `.env`: Compose
  interpolates the overlay's `${SENDER_TRANSPORT:-bluez}` from `.env`, so a value there silently
  overrides it.
- If asked to run `docker compose up --build` or similar: the build now succeeds with no `.env`
  at all (both Drive ids have checked-in defaults in `constants.py`). On WSL or any machine with
  no `/dev/video0`, the compose `devices:` entry fails the run; drop it (or use a `devices:
  !reset []` override) when testing against a video file. Rebuilding does **not**
  refresh an existing `model-cache` volume's contents (see "Model download strategy" → "Gotcha
  this creates") — flag that rather than assuming a rebuild alone picks up a newly trained model.
- The parts pipeline has run end to end in the container on a real face clip (the logs, the
  metrics file and the overlay all checked). It has **not** run on a Pi 5, on real cabin NoIR
  footage, or on drivers the detector never saw; that clip is from its training sources. Don't
  describe its accuracy as validated in code comments, docs, or the titulación report: the
  detector's own test-set numbers (2026-10-20 model, int8: mAP@0.5 0.756, eye-state 0.920,
  head_down AP@0.5 0.743) are per-frame and from one split.
- Don't suggest enabling `OUTPUTS=...,mjpeg` (or otherwise wiring `MjpegStreamOutputStage` in)
  for anything other than a demo. It has no authentication, and this project's own stated
  cargo-theft/security risk model (root `CLAUDE.md`) makes an open, unauthenticated camera
  stream a real exposure for a device that's meant to sit in a truck cabin — not a hypothetical
  one to wave off.
