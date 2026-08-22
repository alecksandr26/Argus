# CLAUDE.md — src/cv-argus

This file provides guidance to Claude Code when working in `src/cv-argus`, the Raspberry Pi 5
edge module of Argus. Read the root `CLAUDE.md` first for the overall system architecture; this
file is the module-specific supplement — exact model input/output shapes, container conventions,
and notebook-fidelity requirements that would otherwise need re-deriving from the notebook every
session. When a task touches this directory, treat the details below as binding, not background.

## What this module is

The computer-vision / AI module of Argus: the code that will run on the Raspberry Pi 5 in
the truck cabin, turning camera frames into a drowsiness class (Alert / Low Vigilant / Drowsy) and handing that
prediction off to whatever decides what to do about it. This is the production counterpart
of the training work in `notebook/01_dataset_creation_lstm.ipynb` → `03_model_training_lstm.ipynb` →
`08_deployment_export_lstm.ipynb` — that LSTM-specific slice of the notebook pipeline produces the
trained model artifact (specifically, `08_deployment_export_lstm.ipynb`'s export step); this module
loads and runs it live. `notebook/ArgusMLModel.ipynb` is the retired monolith the whole pipeline
was originally split from — don't reference it in new work. See `notebook/CLAUDE.md` for the full
pipeline, including the RandomForest/Dense NN/CNN model families this module doesn't consume.

## Current status

Project setup is done: the container substrate (Dockerfile, docker-compose.yml,
requirements.txt, entrypoint script), the Python packaging (`setup.py`, `src/__init__.py`),
and a real entry point (`src/main.py` plus `src/__main__.py`, so it's runnable as
`python -m cv_argus` — the Dockerfile's `CMD` — as well as `python -m cv_argus.main`, or
installed as the `cv-argus-run` console script via `setup.py`'s `entry_points`) all exist.

`model/` is now implemented: `GeometricRatioFeatureLayer` and `LstmGeometricFeatureModel`
ported verbatim from `01_dataset_creation_lstm.ipynb`/`08_deployment_export_lstm.ipynb`
(`layers.py`, `lstm_model.py`), a `gdown`-based downloader
for the trained `.keras` artifact (`downloader.py`, called by the Dockerfile at build time),
and a `DrowsinessDetector` that loads the artifact and exposes
`predict_frame(landmarks_xy, rotation_matrix, blendshape_scores) -> DetectionResult`
(`detector.py`) — deliberately no `mediapipe` import anywhere in this subpackage, see the
`model/` section below. Not wired into `main()` yet — that needs the rest of `pipeline/` to
exist first, since `main()` has nothing to feed the detector frames from.

`pipeline/` exists only as `downloader.py` so far (fetches the MediaPipe `.task` bundle, also
called by the Dockerfile at build time) — the camera-loop/`FaceLandmarker`-wrapper piece is
still unbuilt. `orchestrator/`, `buffer/`, `sender/`, and `alerts/` don't exist yet either —
see "Planned module layout" below for what goes in next, and the exact behavior each part
needs to replicate from the notebook. Wiring the real pipeline into `main()` means replacing
its body, not its signature or how it's invoked.

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
- **Model artifacts (the trained LSTM model, the MediaPipe bundle) are baked into the image at
  *build* time**, not fetched at container start — the Dockerfile runs `python -m
  cv_argus.model.downloader && python -m cv_argus.pipeline.downloader` as a build step, with
  `MODEL_DRIVE_FILE_ID` passed in as a build `ARG` (see `docker-compose.yml`'s `build.args`).
  This device has to be able to boot and start monitoring the driver in a moving truck that
  may have no signal right then — a runtime download dependency is a liability a build-time
  one isn't. Accepted trade-offs: a new trained model needs an image rebuild + redeploy, not
  just a container restart; `docker build`/`docker compose build` now needs network access and
  a valid `MODEL_DRIVE_FILE_ID` to succeed at all (there's no local fallback); and rebuilding
  doesn't reset an *existing* `model-cache` volume's contents (see "Model download strategy"
  below for that gotcha).
- **Docker Compose is used on the Pi too, not just for dev** — it's a thin wrapper around
  `docker build`/`docker run` (no separate daemon, no meaningful overhead on arm64), and
  using the same `docker-compose.yml` in both places avoids hand-retyping flags (volumes,
  device passthrough, env vars) when moving from laptop to cabin. The one difference: the
  Pi deployment should add `restart: unless-stopped` to the service so the container comes
  back up on its own after a power cycle in the truck; that's deliberately left off the dev
  compose file so a laptop container doesn't restart forever while you're iterating on it.

### Running it

```sh
cp .env.example .env   # then fill in MODEL_DRIVE_FILE_ID — required, the build fails without it
docker compose up --build
```

`CAMERA_SOURCE` (env var) is passed straight to `cv2.VideoCapture`: `0` for the first
camera, a `/dev/videoN` path, or a video file path for testing without any camera attached.
The compose file passes through `/dev/video0` by default — adjust the `devices:` entry to
match your machine, or drop it entirely when testing against a video file.

## Model download strategy

Two artifacts get baked into the image at `docker build` time, each fetched by the
subpackage that actually depends on it:

- `model/downloader.py`'s `download_model()` — the trained
  `lstm_geometric_feature_model_<VERSION>.keras`, from `08_deployment_export_lstm.ipynb`'s "Saving
  the Geometric rate feature layer + LSTM Model" step, saved to the user's Google Drive. Fetched via `gdown`
  using a Drive file ID (`MODEL_DRIVE_FILE_ID`, passed as a build `ARG` — see `.env` and
  `docker-compose.yml`'s `build.args`), which requires the file's sharing setting to be
  "Anyone with the link" (Viewer). This was chosen over a Google Cloud service account for zero
  credential management — no GCP setup, no secret to mount on the Pi — at the cost of the model
  file being link-accessible to anyone who has the ID. Revisit this (service account + Drive
  API v3) if that trade-off stops being acceptable.
- `pipeline/downloader.py`'s `download_face_landmarker_bundle()` — the pretrained MediaPipe
  Face Landmarker `.task` bundle, from Google's public model URL, same one
  `01_dataset_creation_lstm.ipynb` downloads via plain `urllib.request` in "Downloading and Setting
  Up the MediaPipe Model Bundle". No credentials or sharing settings involved; it's a public,
  unauthenticated download, so it needs no build arg.

The Dockerfile runs both (`python -m cv_argus.model.downloader && python -m
cv_argus.pipeline.downloader`) as a single `RUN` step, baking both files into `/app/models`
in the image. **Why build time, not container start:** this device needs to boot and start
monitoring the driver even if the truck has no signal at that exact moment — a runtime
download dependency is a liability a build-time one isn't. Accepted trade-off: a new trained
model needs an image rebuild + redeploy, not just a restart, and `docker build` itself now
needs network access and a valid `MODEL_DRIVE_FILE_ID`.

**Gotcha this creates:** `/app/models` is still declared as a volume (`model-cache` in
`docker-compose.yml`), kept as a manual escape hatch for dropping a newer model into a running
container without a rebuild. Docker only seeds a named volume from the image's contents the
*first* time it's created empty — so if you already have a `model-cache` volume from before
(e.g. from testing the previous run-time-download design), rebuilding the image with a new
`MODEL_DRIVE_FILE_ID` will **not** overwrite what's already in that volume; the stale file
wins. Run `docker compose down -v` (or `docker volume rm <project>_model-cache`) to clear it
before rebuilding if you need the new build's files to actually take effect.

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
├── model/          # DONE — GeometricRatioFeatureLayer + LstmGeometricFeatureModel defs, the
│                   # gdown downloader, and the DrowsinessDetector predict wrapper
├── pipeline/       # PARTIAL — downloader.py (the .task bundle fetch) done; the MediaPipe
│                   # FaceLandmarker wrapper + camera capture loop that feeds it still isn't
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

### `model/` — done, must match the notebook's classes exactly

The sequence model stays an LSTM — this had briefly been under evaluation against a plain
feedforward deep neural network over the same windowed features, but a detailed review (see the
root `CLAUDE.md`) confirmed the LSTM and settled it: a plain (non-`stateful`) `LSTM` already
performs genuine recurrence *within* each call, which a Dense layer structurally can't
replicate. The LSTM-specific details below (the `(1, 60, 59)` feature buffer, the two-`LSTM`-
layer stack) are current, not provisional.

`LstmGeometricFeatureModel` is a custom Keras `Model` subclass and `GeometricRatioFeatureLayer`
a custom `Layer` subclass. Loading a saved model that uses custom classes requires the
*exact same class definitions* to be importable at load time and passed as `custom_objects`
to `tf.keras.models.load_model()` — Keras can't reconstruct arbitrary custom `call()` logic
from the saved file alone. **This is also why `layers.py` and `lstm_model.py` can't be deleted
even though the trained model is fetched at runtime, not built locally** — `custom_objects`
needs these classes importable *from this codebase* to reconstruct the downloaded `.keras`
file; without them `load_model()` fails immediately with an unknown-layer error, before the
download step is even relevant. `GeometricRatioFeatureLayer` must be ported verbatim from
`01_dataset_creation_lstm.ipynb` (where it's the source of truth — search for `class
GeometricRatioFeatureLayer`; it's redefined byte-identical in `02_dataset_creation_flat.ipynb`
and `08_deployment_export_lstm.ipynb` too, and this file is the fourth of those four copies,
per the root `CLAUDE.md`).
`LstmGeometricFeatureModel` must be ported verbatim from `08_deployment_export_lstm.ipynb` (search
for `class LstmGeometricFeatureModel`). Include the `blendshape_names` class attribute (52
ARKit-style names, set on `GeometricRatioFeatureLayer` in `01_dataset_creation_lstm.ipynb`'s
"Initial Setup and Blendshape Names" cell) and the `pose_validity_threshold_deg=20.0` default.

Key behavior to know when wiring the predict wrapper around it:

- **It's already stateful — but only the raw-feature buffer is, not the LSTM itself.** The
  model holds a `tf.Variable feature_buffer` of shape `(1, max_timesteps, num_features=58)`
  internally; each call shifts it left by one frame and appends the new one. The LSTM layers
  inside are plain `tf.keras.layers.LSTM` (no `stateful=True` — see
  `03_model_training_lstm.ipynb`'s "LSTM Model Definition" cell, and the "Design Decision:
  Persistent (`stateful=True`) Hidden State" markdown cell right before it, which documents why
  persistent cross-call hidden state was evaluated and deliberately deferred — read that before
  proposing it again), so every call still re-runs the full LSTM stack over all 60 buffered
  frames from a fresh zero hidden state; there's no cheaper incremental RNN update available
  without retraining/re-saving a different architecture. Net effect: `model/` should call it
  once per incoming frame, not accumulate a window itself — the buffering already happens
  inside the model, it just isn't free.
- **Input** is a dict, not positional args:
  `{'landmarks': (1, 478, 2), 'rotation_matrix': (1, 3, 3), 'blendshapes': (1, 52)}` — raw
  MediaPipe outputs for a single frame, batch size fixed at 1.
- **Output** is a softmax over 3 classes (0-indexed; add 1 to get the drowsiness class 1-3, i.e. Alert/Low Vigilant/Drowsy),
  since the internal buffer already represents the trailing `max_timesteps` of history —
  there's no separate "wait until the window fills" step needed before predictions are
  meaningful, though early predictions (buffer still mostly zero-padded) will be less
  reliable until ~6 seconds of frames have been fed in.

Implemented as `layers.py` (`GeometricRatioFeatureLayer`), `lstm_model.py`
(`LstmGeometricFeatureModel`), `downloader.py` (`download_model()` for the `.keras` artifact
via `gdown`, reads `MODEL_DRIVE_FILE_ID`/`MODEL_DIR`/`MODEL_FILENAME` from the environment,
skips re-downloading if the file's already cached; `python -m cv_argus.model.downloader` is
what the Dockerfile calls at build time — see "Model download strategy" above), and
`detector.py` (`DrowsinessDetector` — `from_env()` to
download-then-load per the env vars above, or `from_path()` for an explicit path;
`predict_frame(landmarks_xy, rotation_matrix, blendshape_scores)` takes plain `numpy`/`dict`
arguments, not a MediaPipe result object — **`model/` has no `mediapipe` import at all**, by
design; `pipeline/` owns pulling those three fields out of a `FaceLandmarkerResult` and is
where that translation belongs, so `model/` stays unit-testable with synthetic arrays and
`pipeline/`'s camera backend can change without touching this subpackage; `reset()` zeros the
internal buffer, not called automatically — there for a caller that wants a clean slate after
a long face-loss gap).

### `pipeline/` — follow the notebook's live-simulation pattern, not `LIVE_STREAM` mode

`downloader.py` is the one piece of `pipeline/` that exists so far: `download_face_landmarker_bundle()`
fetches the `.task` bundle from Google's public URL into `MODEL_DIR/face_landmarker.task`,
called by the Dockerfile at build time (`python -m cv_argus.pipeline.downloader` — see "Model
download strategy" above) so it's already sitting there by the time the camera loop needs it.
The camera-loop/`FaceLandmarker`-wrapper piece itself — the part that actually makes `pipeline/`
useful — is still unbuilt; it just needs to point `FaceLandmarkerOptions`'s base options at
that already-downloaded path, not fetch the bundle itself.

Also `pipeline/`'s job, not `model/`'s: pulling `landmarks_xy`, `rotation_matrix`, and
`blendshape_scores` out of a `FaceLandmarkerResult` and calling
`DrowsinessDetector.predict_frame(...)` with them — see the `model/` section above for why that
translation deliberately lives here instead. `01_dataset_creation_lstm.ipynb` downloads
`face_landmarker.task` directly over HTTP (public URL, no auth — see its "Downloading and
Setting Up the MediaPipe Model Bundle" cell) and configures `FaceLandmarkerOptions` with
`running_mode=vision.RunningMode.VIDEO`, `num_faces=1`, detection/presence/tracking confidence
thresholds of `0.5`, and both
`output_face_blendshapes` and `output_facial_transformation_matrixes` enabled. For live
camera frames, replicate `08_deployment_export_lstm.ipynb`'s "End-to-End Live Stream Simulation"
cell: keep using `VIDEO` mode and call `detect_for_video(mp_image, timestamp_ms)` with a monotonically
increasing timestamp (wall-clock based, since there's no fixed source FPS from a live
camera) — rather than switching to MediaPipe's `LIVE_STREAM` callback mode, which is a
different API shape that was never validated in the notebook.

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
  module, rather than raw `/dev/video*` + `libcamera` device passthrough — still worth a
  dedicated smoke test on real Pi hardware early, since `picamera2` inside a container has
  its own passthrough requirements (typically `/dev/video*` plus `/dev/dma_heap/*`) that
  haven't been verified in this repo yet. A dev laptop's USB webcam keeps using plain
  `cv2.VideoCapture` either way.

## `scripts/` — dataset-prep utilities, not part of the deployed package

Not imported by `cv_argus` at runtime — these are standalone scripts you run locally, by hand,
before uploading anything to Drive. Currently just `extract_uta_rldd_clips.py`: reads a
downloaded [UTA-RLDD](https://sites.google.com/view/utarldd/home) zip archive (e.g.
`Fold1_part1.zip`) directly (never extracts the whole ~13GB archive at once — pulls one
~10-minute source video to a temp file, processes it, discards it, moves on), cuts a few short
random non-overlapping sub-clips per source video via `ffmpeg`/`ffprobe`, and writes them as
`level_<1-3>_clip_<N>.mp4` into new `subject_<N>` folders (continuing after
`notebook/01_dataset_creation_lstm.ipynb`'s existing `subject_01`..`subject_06`, `subject_07` onward
by default via `--start-subject`) under `scripts/output/` — gitignored, since it's large binary
video output, not something to commit.

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
| `model/` | **done** — LSTM/geometric-feature inference, gdown model download |
| `pipeline/` | **partial** — `.task` bundle download done; camera + FaceLandmarker capture loop pending |
| `orchestrator/` | decides send-or-not, builds an `Alert` |
| `buffer/` | saving and queuing ONLY (SQLite, sent/unsent tracking) — no comms |
| `sender/` | owns communication setup, dequeues from `buffer/`, transmits, reports success back |
| `alerts/` | `Alert` data model + serialization only |

Four of the six (`orchestrator/`, `buffer/`, `sender/`, `alerts/`) don't exist as code yet;
`pipeline/` exists only as its `downloader.py`. What's otherwise in place: `model/` itself, the
container (Dockerfile, docker-compose.yml, two volumes for the model cache and the SQLite
buffer — both artifacts now baked in at build time, see "Model download strategy"), the
packaging (`setup.py` mapping `src/` to the `cv_argus` import name), and a real entry point
(`src/main.py` + `src/__main__.py`, run via `python -m cv_argus`, `python -m cv_argus.main`, or
the `cv-argus-run` console script) that currently just verifies the environment and isn't yet
wired to call into `model/`. Design work on the remaining modules continues outside this repo
— pick up from this file rather than re-deriving the plan from scratch.

## Working in this module

- Before touching `model/`, re-read "`model/` — done, must match the notebook's classes exactly"
  above. `GeometricRatioFeatureLayer` and `LstmGeometricFeatureModel` must stay byte-identical
  ports of the notebook's classes (source of truth: `01_dataset_creation_lstm.ipynb` and
  `08_deployment_export_lstm.ipynb` respectively) — don't refactor their internals for style or
  add convenience arguments without re-verifying against the notebook cells first, since a
  mismatch breaks `custom_objects` deserialization of the downloaded `.keras` file silently or
  loudly depending on what changed.
- Don't add a `mediapipe` import to anything under `model/`. That boundary is deliberate (see
  the `model/` section) — `pipeline/` owns translating a `FaceLandmarkerResult` into the plain
  `numpy`/`dict` arguments `DrowsinessDetector.predict_frame()` takes.
- Don't move CAN-bus/alarm/panic-button/geolocation logic into this module — that's the ESP32's
  job per the root `CLAUDE.md`. This module only decides and queues; `sender/` (once built) is
  the boundary, not a place to reach across it.
- When building the camera loop in `pipeline/`, follow `08_deployment_export_lstm.ipynb`'s
  "End-to-End Live Stream Simulation" cell (`VIDEO` running mode, `detect_for_video` with a
  monotonically increasing wall-clock timestamp) — don't switch to MediaPipe's `LIVE_STREAM`
  callback API; it's a different shape that was never validated in the notebook.
- When building `orchestrator/`, `buffer/`, `sender/`, or `alerts/`, build against the small
  interface described in their section above (a logging/no-op `Transport` implementation is
  fine to start) rather than blocking on the still-open Bluetooth polling-shape question — see
  "Open decisions" before assuming a simple push-based `Transport.send(alert) -> bool` shape.
- If asked to run `docker compose up --build` or similar, remember the build fails without a
  real `MODEL_DRIVE_FILE_ID` in `.env`, and that rebuilding does **not** refresh an existing
  `model-cache` volume's contents (see "Model download strategy" → "Gotcha this creates") —
  flag that rather than assuming a rebuild alone picks up a newly trained model.
