# CLAUDE.md — src/cv-argus

This file provides guidance to Claude Code when working in `src/cv-argus`, the Raspberry Pi 5
edge module of Argus. Read the root `CLAUDE.md` first for the overall system architecture; this
file is the module-specific supplement — exact model input/output shapes, container conventions,
and notebook-fidelity requirements that would otherwise need re-deriving from the notebook every
session. When a task touches this directory, treat the details below as binding, not background.

## What this module is

The computer-vision / AI module of Argus: the code that will run on the Raspberry Pi 5 in
the truck cabin, turning camera frames into a drowsiness class and handing that
prediction off to whatever decides what to do about it. **The project's class scheme is
binary — `Not Drowsy` vs. `Drowsy`**, and this module now labels binary too:
`detector.py`'s `_CLASS_NAMES` and `mjpeg_output_stage.py`'s status-colour map are both
`{Not Drowsy, Drowsy}`.

**This module deploys exactly one pipeline: the binary frozen-CNN-embedding +
geometric-feature-fusion + LSTM classifier** (`notebook/11_cnn_lstm_training_drive_pull.ipynb`'s
frozen-embedding variant). End to end: a MediaPipe BlazeFace face crop → a frozen
**convolutional** network's 64-dim penultimate embedding, fused per frame with a 10-feature
MediaPipe FaceLandmarker geometric subset (EAR/MAR ratios + selected blendshapes) → an **LSTM**
over a rolling window of up to 100 frames, output **`Not Drowsy` vs. `Drowsy`**. See "Current
status" below for its measured accuracy and open caveats. Two earlier pipelines — a single-frame
CNN (`notebook/07_cnn_training.ipynb`, deployed by itself for a while) and a
windowed-geometric-only LSTM (`notebook/01_dataset_creation_lstm.ipynb` →
`03_model_training_lstm.ipynb` → `08_deployment_export_lstm.ipynb`) — were removed once this
result made both obsolete (see the root `CLAUDE.md`): the single-frame CNN's own macro-F1
(0.5273) was roughly half this model's (0.8375) on the same held-out subjects, and the
geometric-only LSTM was never deployed to begin with. The CNN checkpoint from the first of those
two is still downloaded and loaded, though — not for its own classification, but as the frozen
convolutional embedding backbone this model's LSTM was trained on top of (see `cnn_detector.py`).
`notebook/ArgusMLModel.ipynb` is the retired monolith the whole pipeline was originally split
from — don't reference it in new work. See `notebook/CLAUDE.md` for the full pipeline this
module now draws on (the CNN and CNN+LSTM model families it deploys, and the
RandomForest/Dense NN/windowed-LSTM baselines it doesn't).

## Current status

Project setup is done: the container substrate (Dockerfile, docker-compose.yml,
requirements.txt, entrypoint script), the Python packaging (`setup.py`, `src/__init__.py`),
and a real entry point (`src/main.py` plus `src/__main__.py`, so it's runnable as
`python -m cv_argus` — the Dockerfile's `CMD` — as well as `python -m cv_argus.main`, or
installed as the `cv-argus-run` console script via `setup.py`'s `entry_points`) all exist.

**This module deploys one pipeline: the binary frozen-CNN-embedding +
geometric-feature-fusion + LSTM classifier, `notebook/11_cnn_lstm_training_drive_pull.ipynb`'s
frozen-embedding variant.** Measured **84.24% test accuracy / 0.8375 macro-F1** (binary
`Not Drowsy` / `Drowsy`) on held-out subjects — by far the best result in the project, roughly
double the single-frame CNN's own macro-F1 (0.5273) on the same subjects, which is why that
earlier pipeline (and the never-deployed windowed-geometric-only LSTM before it) was removed
rather than kept as an alternative — see "What this module is" above and the root `CLAUDE.md`.
**Not yet cross-validated**: one subject-grouped train/val/test split, no k-fold, no variance
estimate.

`model/fused_detector.py`'s `FusedDrowsinessDetector` and `model/fused_features.py` (the
10-feature geometric subset this model fuses with the CNN embedding) load two artifacts: the
fused `.keras` model itself (`FUSED_MODEL_DRIVE_FILE_ID`, checked into `constants.py`) and the
CNN checkpoint from `notebook/07_cnn_training.ipynb` (`CNN_MODEL_DRIVE_FILE_ID`, also checked
in) — reused purely as a frozen feature extractor via `CnnDrowsinessDetector
.embedding_submodel()`, which exposes the CNN's penultimate `Dense(64, relu)` layer instead of
its final softmax; the CNN is never run for its own classification in this module anymore. **A
real, still-open risk, not yet resolved**: `notebook/11` trained against
`best_cnn_scratch_face_crops.keras`, a specific checkpoint that hasn't been hash-verified as
identical to what `CNN_MODEL_DRIVE_FILE_ID` actually points at — if they're different training
runs, the fused pipeline's live embeddings won't match what the LSTM learned on, a silent
accuracy bug, not a crash. See `src/dataset/tests/test_fused_features_equiv.py` for the
feature-math equivalence test (verified passing against real crop files + a real FaceLandmarker
in this session) — that test does *not* cover this CNN-checkpoint-parity risk, only the
geometric-feature computation. The window buffer lives on `FusedDrowsinessDetector` itself as a
plain `numpy` array — see that class's module docstring for the full statefulness design, and
`FaceLandmarkerCropStage`'s module docstring for why it runs FaceLandmarker in `IMAGE` mode on
the *crop* (not `VIDEO` mode on the full frame), which is a training-fidelity requirement, not a
style choice.

`pipeline/` is implemented as a threaded, queue-connected `Stage`/`Pipeline` abstraction
(`stage.py`) with `FaceDetectorCropStage` (BlazeFace) → `FaceLandmarkerCropStage` (geometric
features, on the crop) → `FusedInferenceStage` (wraps `FusedDrowsinessDetector`) for MediaPipe
and inference, `VideoCaptureSource`/`PiCameraSource` for frame sources, and
`LoggingOutputStage`/`MjpegStreamOutputStage` (a demo-only, browser-viewable annotated stream —
see "`pipeline/` — done" below for the security caveat) as sinks. `src/main.py` is fully wired
to it: `SOURCE` (`video_capture` default / `picamera`) picks where frames come from, `OUTPUTS`
(comma-separated, `logging` default) picks one or more sinks — fanned out from the same
inference stage via `Stage.connect()`, not a new mechanism. See "`pipeline/` — done" below and
"Running it" below for the demo procedure. `orchestrator/`, `buffer/`, `sender/`, and `alerts/`
still don't exist — see "Planned module layout" below for what goes in next, and the exact
behavior each part needs to replicate from the notebook.

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
- **Model artifacts (the fused CNN+LSTM model, the CNN checkpoint it depends on, the two
  MediaPipe bundles) are baked into the image at *build* time**, not fetched at container start —
  the Dockerfile runs `python -m cv_argus.model.downloader && python -m
  cv_argus.pipeline.downloader` as a build step, with `CNN_MODEL_DRIVE_FILE_ID`/
  `FUSED_MODEL_DRIVE_FILE_ID` passed in as build `ARG`s (see `docker-compose.yml`'s
  `build.args`) — both have checked-in defaults, so no `.env` file is required. This device has
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

`CAMERA_SOURCE` (env var) is passed straight to `cv2.VideoCapture`: `0` for the first
camera, a `/dev/videoN` path, or a video file path for testing without any camera attached.
The compose file passes through `/dev/video0` by default — adjust the `devices:` entry to
match your machine, or drop it entirely when testing against a video file.

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
  as the reliable fallback. Pi 5 CPU performance for BlazeFace+CNN at a usable fps is also
  unmeasured — no hardware has been available to benchmark it from this codebase's sessions.

## Model download strategy

Artifacts get baked into the image at `docker build` time, each fetched by the subpackage that
actually depends on it. Drive file IDs and bundle URLs live in `constants.py` (see its own
docstring) rather than as scattered inline defaults — every env var below falls back to a
constant there if unset (an empty-string env var, e.g. from an unset Docker build `ARG`, is
treated the same as an absent one, not as an explicit override).

- `model/downloader.py`'s `download_cnn_model()` — the trained `cnn_face_crop_model.keras`
  (from `07_cnn_training.ipynb`). Not run for its own classification anymore (see "Current
  status" above) — required because `download_fused_model()`'s model depends on it as a frozen
  embedding backbone. Fetched via `gdown` using a Drive file ID (`CNN_MODEL_DRIVE_FILE_ID` env
  var, defaulting to `constants.py`'s checked-in id — the file is shared "Anyone with the link",
  not a secret, so it's fine to default from source rather than require a build `ARG`/`.env`
  entry). Same gdown-over-service-account trade-off as before: zero credential management at the
  cost of the file being link-accessible to anyone with the ID. Revisit this (service account +
  Drive API v3) if that stops being acceptable.
- `pipeline/downloader.py`'s `download_face_landmarker_bundle()` — the pretrained MediaPipe
  Face Landmarker `.task` bundle `FaceLandmarkerCropStage` needs. Public, unauthenticated
  download from Google's model URL, same one `01_dataset_creation_lstm.ipynb` downloads via
  plain `urllib.request`; no build arg needed.
- `pipeline/downloader.py`'s `download_face_detector_bundle()` — the pretrained MediaPipe Face
  Detector (BlazeFace, short_range/float16) `.tflite` bundle `FaceDetectorCropStage` needs — a
  different, lighter bundle from the Landmarker one above (bounding-box-only, no landmarks),
  matching `06_dataset_creation_face_crops.ipynb`'s "MediaPipe Face Detector Setup" cell. Same
  public/unauthenticated shape; unlike the CNN *model* artifact above, there's no "is this a
  trustworthy artifact" question gating it.
- `model/downloader.py`'s `download_fused_model()` — the trained
  `best_cnn_lstm_frozen_embedding.keras` (from `11_cnn_lstm_training_drive_pull.ipynb`'s
  frozen-embedding variant), **the model this container deploys** (see "Current status" above).
  Fetched via `gdown` the same way as the CNN model, with its own checked-in default Drive id
  (`FUSED_MODEL_DRIVE_FILE_ID`). Required, same as the CNN download — a failure in either fails
  the whole build (see `model/downloader.py`'s `__main__` block), since a container that can't
  load this model can't do anything useful.

The Dockerfile runs `python -m cv_argus.model.downloader && python -m
cv_argus.pipeline.downloader` as a single `RUN` step. **Why build time, not container start:**
this device needs to boot and start monitoring the driver even if the truck has no signal at
that exact moment — a runtime download dependency is a liability a build-time one isn't.
Accepted trade-off: a new trained model needs an image rebuild + redeploy, not just a restart.
`docker build` succeeds with **no `.env` file at all** — both the CNN and fused models' default
Drive ids are checked into `constants.py`.

**Gotcha this creates:** `/app/models` is still declared as a volume (`model-cache` in
`docker-compose.yml`), kept as a manual escape hatch for dropping a newer model into a running
container without a rebuild. Docker only seeds a named volume from the image's contents the
*first* time it's created empty — so if you already have a `model-cache` volume from before
(e.g. from testing the previous run-time-download design), rebuilding the image with a new
`FUSED_MODEL_DRIVE_FILE_ID`/`CNN_MODEL_DRIVE_FILE_ID` will **not** overwrite what's already in
that volume; the stale file wins. Run `docker compose down -v` (or `docker volume rm
<project>_model-cache`) to clear it before rebuilding if you need the new build's files to
actually take effect.

## `buffer/`'s SQLite file needs a volume, not a container

SQLite isn't a server — there's no daemon, no port, nothing to add as a separate
`docker-compose.yml` service. It's a library (Python's stdlib `sqlite3`) that opens a single
file directly from within whatever process calls it, so `buffer/` will just read/write a
file path from inside the existing `cv-argus` container, same as any other local file.

The one thing that *does* need infrastructure: a container's filesystem is thrown away on
rebuild, so that file needs to live outside the container image the same way the downloaded
model does. `BUFFER_DIR` (`/app/data` by default, see `.env.example`) is a dedicated named
volume (`buffer-data`, separate from `model-cache`) for exactly this — the queue and the
model cache have different lifecycles (e.g. you may want to wipe/back up the alert queue
without touching the downloaded model, or vice versa).

One implementation note for whoever builds `buffer/`: if `orchestrator/` (writing new
alerts) and `sender/` (reading/marking them sent) end up touching the file from different
threads, open the connection with `PRAGMA journal_mode=WAL` — SQLite's default journal mode
serializes readers behind a writer more aggressively than WAL does, and this file will
plausibly have both happening around the same time.

## Planned module layout

```
src/
├── __init__.py
├── model/          # DONE — GeometricRatioFeatureLayer (EAR/MAR/pose math), the gdown downloader
│                   # (the CNN checkpoint + the deployed fused model), CnnDrowsinessDetector
│                   # (frozen embedding backbone loader) and FusedDrowsinessDetector (the
│                   # deployed model's predict wrapper)
├── pipeline/       # DONE — threaded Stage/Pipeline abstraction (stage.py), sources
│                   # (camera/video file/Pi CSI), the two MediaPipe stages, the inference
│                   # stage, and both bundle downloads; wired into src/main.py
├── orchestrator/   # decision logic: given a prediction (+ later, other signals like the
│                   # grip sensor), decides whether it's worth raising an alert at all
│                   # (thresholds, debounce/hysteresis across frames, cooldowns) — the
│                   # "Alert/RouteStatus Orchestration (Decision Making)" box in the design
│                   # diagram. Builds an Alert (via alerts/) and hands it to buffer/.
├── buffer/         # saving and queuing only: persists Alerts to SQLite (enqueue) and tracks
│                   # sent/unsent state — the "Queue Message Local Buffer (SQLite)" box in the
│                   # design diagram, and the answer to "what happens when the truck goes
│                   # offline". No communication logic of its own — sender/ dequeues from it.
├── sender/         # owns the actual communication with the ESP32 — now decided as Bluetooth,
│                   # with the ESP32 polling: this is likely a small server side that answers
│                   # the ESP32's periodic pulls (unsent alerts, then sent/unsent acks) rather
│                   # than a component that pushes on its own schedule — see "Open decisions"
│                   # below before assuming the push-based shape implied by earlier notes here.
└── alerts/         # data model + serialization only for an Alert record (level,
                     # probabilities, timestamp, geolocation, ...) — no logic, no persistence;
                     # orchestrator/, buffer/, and sender/ all depend on this, not vice versa
```

Internal file names within each subpackage aren't decided yet — the notes below describe
required *behavior*, to carry forward regardless of how the files end up split up.

### `model/` — done: a shared geometric-feature layer plus the fused detector's two backbones

`GeometricRatioFeatureLayer` (`layers.py`) computes EAR/MAR/pose ratios from raw MediaPipe
landmarks — ported verbatim from `01_dataset_creation_lstm.ipynb` (the source of truth; also
redefined byte-identical in `02_dataset_creation_flat.ipynb` and `08_deployment_export_lstm
.ipynb`, notebooks this module no longer deploys against but that still exist as training
reference — per the root `CLAUDE.md`). `fused_features.py` calls this layer directly (never
deserializes it as part of a saved model) to compute the 10-feature geometric subset the fused
model fuses with the CNN embedding — see `model/`'s fused detector section below for the full
shape.

Implemented as `layers.py` (`GeometricRatioFeatureLayer`), `downloader.py`
(`download_cnn_model()`/`download_fused_model()` — both required at build time, see "Model
download strategy" above), `cnn_detector.py` (`CnnDrowsinessDetector` — loads the CNN checkpoint
and exposes its frozen embedding sub-model, see below), and `fused_detector.py`
(`FusedDrowsinessDetector` — the actual deployed predict wrapper, see below).

### `model/`'s fused detector — done

`FusedDrowsinessDetector` (`fused_detector.py`) loads `best_cnn_lstm_frozen_embedding.keras`
(`notebook/11_cnn_lstm_training_drive_pull.ipynb`'s frozen-embedding variant — see "Current
status" above for the measured accuracy and its caveats). The loaded `.keras` graph has **no
internal state at all** — confirmed against the notebook: it's built from stock
`Input`/`Normalization`/`LSTM`/`Dropout`/`Dense` layers only (no custom `Layer`/`Model`
subclass, no `custom_objects` needed to load it), taking a pre-assembled `(max_timesteps=100,
fused_dim=74)` array and a `(100,)` boolean mask as two *external* inputs. So the sliding window
has to live on the Python wrapper instead, as a plain `numpy` array (`self._buffer`/
`self._mask`) — one `FusedDrowsinessDetector` instance per camera stream; `predict_frame()` must
be called once per incoming frame, in order, since that buffer is this class's only memory.

Key behavior:

- **Two artifacts load into one detector**: the fused `.keras` model itself, and `07`'s CNN
  checkpoint (loaded via `CnnDrowsinessDetector.embedding_submodel()` — see `cnn_detector.py`),
  used purely as a frozen 64-dim feature extractor, never for its own classification. **These
  two artifacts must come from matched training runs** — see "Current status" above for why a
  mismatch would be a silent accuracy bug.
- **The buffer holds numbers, not images.** Each frame's face crop is embedded once (64 floats),
  fused with a 10-float geometric-feature vector (`fused_features.py`) into one 74-float row,
  and only that row is kept — the crop's pixels are never stored. A 100-row buffer is ~30KB.
- **Padding convention: zero-*pre*-padded**, zeros first, real frames last — the opposite of a
  naive "append at the front" ring buffer. Each `predict_frame()` call does `np.roll(buffer,
  -1)` then overwrites the *last* slot with the new frame, matching exactly how
  `notebook/11`'s `build_fused_arrays` constructs training windows
  (`X[i, pad_amount:] = fused`). Getting this backwards doesn't raise an exception — it silently
  feeds the model right-padded sequences it was never trained on.
- **`use_cudnn=False` is forced on the loaded LSTM layer post-load** (`fused_detector.py`'s
  `_force_no_cudnn()`) — required because of the pre-pad convention above (Keras' cuDNN fast
  path assumes right-padding), mirroring `notebook/11`'s own `evaluate_variant`/`_load_for_eval`.
  Irrelevant for raw speed on a Pi (no cuDNN there anyway) but required for correctness.
- **Input**: `predict_frame(face_crop_rgb, geo_features)` — an already-cropped RGB image plus an
  already-computed 10-dim geometric-feature vector (`fused_features.compute_fused_geo_features()`,
  or `zero_fused_geo_features()` on a landmarker miss) — never a MediaPipe result object; that
  translation is `pipeline/`'s job (see `model/`-has-no-`mediapipe`-import in `fused_detector
  .py`'s module docstring).
- **Output/level**: a *threshold* on `p(Drowsy)` (`constants.FUSED_MODEL_THRESHOLD`, checked in
  from `notebook/11`'s operating-point cell — see "Model download strategy" above for why it's
  checked in rather than fetched from Drive), **not `argmax`**. `level=2`/`"Drowsy"` and
  `level=1`/`"Not Drowsy"` render via `detector.py`'s module-level `_CLASS_NAMES` dict.

### `pipeline/` — done, a threaded `Stage`/`Pipeline` abstraction

Built as a small pipe-and-filter framework rather than one monolithic camera loop, specifically
so a future model family could share the same plumbing instead of reimplementing
threading/queueing/shutdown from scratch — this is also what let the earlier CNN and LSTM
pipelines share it before they were removed in favor of the fused one:

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
- **`face_detector_stage.py`** (`FaceDetectorCropStage`) / **`face_landmarker_crop_stage.py`**
  (`FaceLandmarkerCropStage`, `IMAGE` mode on the crop `FaceDetectorCropStage` produced — see its
  module docstring for why this must run on the crop and in `IMAGE` mode specifically, a
  training-fidelity requirement, not a style choice) — the two MediaPipe stages, chained one
  after the other. `FaceDetectorCropStage` uses `VIDEO` running mode and `detect_for_video
  (mp_image, timestamp_ms)` with a monotonically increasing wall-clock timestamp, matching the
  notebooks' live-simulation pattern — not MediaPipe's `LIVE_STREAM` callback API, a different
  shape never validated in any notebook. It ports `06_dataset_creation_face_crops.ipynb`'s crop
  logic verbatim (`bbox_margin_frac=0.25`, `min_detection_confidence=0.5`, highest-confidence
  detection when more than one face is found — both constants live in `constants.py`) and
  explicitly converts the crop from BGR to RGB before handing it downstream — the frozen CNN
  embedder was trained on RGB pixels (via `06`'s `cv2.imwrite`(BGR) -> `07`'s
  `tf.io.decode_jpeg`(RGB) round trip), and there's no JPEG file here to do that conversion
  implicitly. Get this backwards and nothing raises an exception; accuracy just silently
  degrades. Two MediaPipe calls per sampled frame, not one, matching `06`→`09`'s two-stage
  extraction exactly rather than deriving a crop from FaceLandmarker's own landmark-extent bbox
  (which would be a different, unvalidated feature-extraction path).
- **`inference_stages.py`** — `FusedInferenceStage`, a thin wrapper around `model/`'s
  `FusedDrowsinessDetector`. Pulling MediaPipe output into the plain `numpy` arguments the
  detector expects happens in the MediaPipe stages above, not here — see
  `model/fused_detector.py` for why that boundary is deliberate. `FusedInferenceStage` no-ops
  (doesn't advance the window buffer) on a frame with no face crop at all — the project's chosen
  behavior for a totally-missed BlazeFace detection, see that stage's docstring.
- **`output_stages.py`** — `LoggingOutputStage`, the placeholder sink until `orchestrator/`
  exists (reuses the "logging/no-op... fine to start" plan from the section below).
- **`mjpeg_output_stage.py`** — `MjpegStreamOutputStage`, a demo-only sink: draws
  `draw_detection_overlay()` (face box from `ctx.features["face_bbox"]`, color-coded status,
  fps) onto each frame and serves it as an MJPEG-over-HTTP stream (stdlib `http.server` only,
  no new pip dependency) — viewable from any device's browser on the network, with no display
  attached to whatever machine runs the pipeline and no need for non-headless OpenCV/X11 in the
  container. **No authentication** — see "Demo" above for why this defaults off (`OUTPUTS`) and
  isn't for a real deployment. `draw_detection_overlay()` is a standalone function, not a
  method, specifically so a future stage (e.g. "save the annotated demo to a video file") can
  reuse it without the HTTP-serving machinery — this was the "future video-overlay output
  stage" extension point mentioned in earlier notes here; it's now built, not just planned.
  `FaceDetectorCropStage` populates `ctx.features["face_bbox"]` so this stage's box-drawing has
  something to work with.
- **`downloader.py`** — `download_face_landmarker_bundle()` and `download_face_detector_bundle()`,
  both called from `src/main.py`'s pipeline builder as well as at Docker build time — see "Model
  download strategy" above.

`pipeline/__init__.py` re-exports all of the above, but lazily for anything needing `cv2`/
`mediapipe`/`tensorflow` (everything except `stage.py`, `downloader.py`, and
`output_stages.py`) — see that file's module docstring for why: it keeps `Stage`/`Pipeline`/
`FrameContext` importable and testable without the full stack installed.

`src/main.py` wires all of this together: `SOURCE` picks where frames come from
(`video_capture` default / `picamera`), and `OUTPUTS` picks one or more sinks (comma-separated,
`logging` default) — connected to the same inference stage via repeated `Stage.connect()` calls
(fan-out), not a new mechanism. There's one pipeline shape now, built by `_build_pipeline()` —
no `PIPELINE` env var to pick between model families anymore (see "Current status" above for
why the earlier `cnn`/`lstm` options were removed). Runs until a signal arrives or the source
(e.g. a video file) ends on its own — see `Pipeline.is_alive()` in `stage.py`.

### `orchestrator/`, `buffer/`, `sender/`, `alerts/` — intentionally stubbed

None of these four are designed yet beyond the split of responsibility above. What's known so
far is the data flow: `orchestrator/` decides send-or-not → builds an `Alert` using
`alerts/`'s model → hands it to `buffer/`, which only saves and queues it (SQLite, tracks
sent/unsent) → `sender/` is what actually talks to the ESP32 described in `docs/designs/semantic-design*`
(that hardware and firmware don't exist in this repo), reporting success back to `buffer/` so
an item can be removed from the queue once the ESP32 has it. The wire protocol is no longer
fully open — Bluetooth, ESP32-initiated polling (see "Open decisions" below) — but the exact
shape of `sender/`'s interface still is, since polling flips who calls whom compared to a
simple `Transport.send(alert) -> bool`. Build against a small interface with a logging/no-op
implementation for now regardless of the final shape, so the rest of the pipeline has
somewhere to hand off predictions without blocking on that design.

## Open decisions that affect this module later

- **Pi ↔ ESP32 transport: decided as Bluetooth, with the ESP32 as the polling side.** The
  ESP32 periodically connects and pulls unsent records from the Pi's SQLite buffer — the Pi
  doesn't push. This flips the assumption `sender/`'s description above was written under
  (that it "dequeues from `buffer/` ... transmits them"): if the ESP32 is the one initiating
  each pull, `sender/` is closer to a small Bluetooth *server* that answers "give me unsent
  alerts" / "mark these as sent" requests than to a component that proactively pushes on its
  own schedule. Worth settling this shape explicitly before writing `sender/`, since it changes
  what the `Transport` interface mentioned below needs to look like. Container-wise this also
  means passing through Bluetooth access (e.g. a `/dev/rfcommN` device, or the host's
  BlueZ/D-Bus socket) instead of the `/dev/ttyAMA0`/`/dev/ttyUSB0` UART passthrough a serial
  link would have needed.
- Real camera on the Pi: settled as `picamera2` (see "Python packaging" above) for the CSI
  module, rather than raw `/dev/video*` + `libcamera` device passthrough. `docker-compose.pi.yml`
  now exists with a best-effort device list (`/dev/video0`-`/dev/video3` plus a couple of
  commonly-seen `/dev/dma_heap/*` nodes), but it's explicitly **not verified against real Pi 5
  hardware in this repo** — still worth a dedicated smoke test early (see "Demo" above), and the
  device list may well need adjusting once someone can actually check `ls /dev/video*` /
  `ls /dev/dma_heap/` on the real device. A dev laptop's USB webcam keeps using plain
  `cv2.VideoCapture` either way (`SOURCE=video_capture`, the default).

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

The module breakdown is six pieces, with `buffer/` and `sender/` cleanly split:

| Module | Responsibility |
|---|---|
| `model/` | **done** — fused CNN-embedding/geometric-feature/LSTM inference (the model this container deploys — best measured accuracy in the project, not yet cross-validated) plus the CNN checkpoint loader it depends on as a frozen embedding backbone, gdown download for both |
| `pipeline/` | **done** — threaded Stage/Pipeline abstraction, two MediaPipe stages, one inference-stage wrapper, both bundle downloads; wired into `src/main.py` |
| `orchestrator/` | decides send-or-not, builds an `Alert` |
| `buffer/` | saving and queuing ONLY (SQLite, sent/unsent tracking) — no comms |
| `sender/` | owns communication setup, dequeues from `buffer/`, transmits, reports success back |
| `alerts/` | `Alert` data model + serialization only |

Four of the six (`orchestrator/`, `buffer/`, `sender/`, `alerts/`) still don't exist as code —
`model/` and `pipeline/` are both done now. What's otherwise in place: the container
(Dockerfile, docker-compose.yml, two volumes for the model cache and the SQLite buffer — both
artifacts baked in at build time, see "Model download strategy"), the packaging (`setup.py`
mapping `src/` to the `cv_argus` import name), and a real entry point (`src/main.py` +
`src/__main__.py`, run via `python -m cv_argus`, `python -m cv_argus.main`, or the
`cv-argus-run` console script) now wired to actually build and run the fused `Pipeline` (see
"`pipeline/` — done" above) rather than just verifying the environment. What's still missing to
get an actual alert out of this end to end:
`orchestrator/`, `buffer/`, `sender/`, `alerts/` — `LoggingOutputStage` is the sink until those
exist. Design work on those remaining modules continues outside this repo — pick up from this
file rather than re-deriving the plan from scratch.

## Working in this module

- Before touching `model/`, re-read "`model/` — done" above. `GeometricRatioFeatureLayer` must
  stay a byte-identical port of the notebook's class (source of truth: `01_dataset_creation_lstm
  .ipynb`) — don't refactor its internals for style or add convenience arguments without
  re-verifying against the notebook cells first, since a mismatch silently changes the
  geometric-feature math `fused_features.py` depends on.
- Don't add a `mediapipe` import to anything under `model/`. That boundary is deliberate (see
  the `model/` section) — `pipeline/` owns translating MediaPipe results into the plain `numpy`
  arguments `FusedDrowsinessDetector.predict_frame()` takes.
- Don't move CAN-bus/alarm/panic-button/geolocation logic into this module — that's the ESP32's
  job per the root `CLAUDE.md`. This module only decides and queues; `sender/` (once built) is
  the boundary, not a place to reach across it.
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
- When building `orchestrator/`, `buffer/`, `sender/`, or `alerts/`, build against the small
  interface described in their section above (a logging/no-op `Transport` implementation is
  fine to start) rather than blocking on the still-open Bluetooth polling-shape question — see
  "Open decisions" before assuming a simple push-based `Transport.send(alert) -> bool` shape.
- If asked to run `docker compose up --build` or similar: the build now succeeds with no `.env`
  at all (both Drive ids have checked-in defaults in `constants.py`). Rebuilding does **not**
  refresh an existing `model-cache` volume's contents (see "Model download strategy" → "Gotcha
  this creates") — flag that rather than assuming a rebuild alone picks up a newly trained model.
- The fused pipeline's mechanics are exercised (synthetic-model smoke tests confirmed the window
  buffer fills/caps/resets correctly, `FaceLandmarkerCropStage` extracts real geometric features
  from a real crop via a real cached `face_landmarker.task`, and `src/dataset/tests
  /test_fused_features_equiv.py` passes against real crop files) — but it has **not** been run
  end to end against the real trained `best_cnn_lstm_frozen_embedding.keras` and the real
  deployed CNN checkpoint together, and the CNN-checkpoint-provenance risk in "Current status"
  above is still open. Don't describe this pipeline as validated or ready to deploy without that
  real run.
- The deployed model's real measured number is 84.24% test accuracy / 0.8375 macro-F1, one
  subject-grouped fold, not yet cross-validated (see "Current status" and `notebook/CLAUDE.md`)
  — a real result, not a validated production one. Don't describe it as production-validated in
  code comments, docs, or the titulación report; state it with the same caveat this file does.
- Don't suggest enabling `OUTPUTS=...,mjpeg` (or otherwise wiring `MjpegStreamOutputStage` in)
  for anything other than a demo. It has no authentication, and this project's own stated
  cargo-theft/security risk model (root `CLAUDE.md`) makes an open, unauthenticated camera
  stream a real exposure for a device that's meant to sit in a truck cabin — not a hypothetical
  one to wave off.
