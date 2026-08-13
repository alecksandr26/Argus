# cv-argus

The computer-vision / AI module of Argus: the code that will run on the Raspberry Pi 5 in
the truck cabin, turning camera frames into a drowsiness level (1–6) and handing that
prediction off to whatever decides what to do about it. This is the production counterpart
of the training work in `notebook/ArgusMLModel.ipynb` — the notebook produces the trained
model artifact; this module loads and runs it live.

## Current status

Project setup is done: the container substrate (Dockerfile, docker-compose.yml,
requirements.txt, entrypoint script), the Python packaging (`setup.py`, `src/__init__.py`),
and a real entry point (`src/main.py` plus `src/__main__.py`, so it's runnable as
`python -m cv_argus` — the Dockerfile's `CMD` — as well as `python -m cv_argus.main`, or
installed as the `cv-argus-run` console script via `setup.py`'s `entry_points`) all exist.
`main()` currently only verifies the environment (library versions, `MODEL_DIR`/`BUFFER_DIR`) since
none of the actual pipeline modules exist yet — see "Planned module layout" below for what
goes in next, and the exact behavior each part needs to replicate from the notebook. Wiring
the real pipeline in means replacing `main()`'s body, not its signature or how it's invoked.

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
- **The trained model is meant to be fetched at container *start*, not baked into the
  image** — `/app/models` is already a volume for this. Not wired up yet: whether
  `docker-entrypoint.sh` runs the downloader before `exec`'ing the main command, or
  something else does, is still open (see the comment in `docker-entrypoint.sh`) until
  `model/` exists. Once decided, a new trained model should only need a container restart,
  not an image rebuild.
- **Docker Compose is used on the Pi too, not just for dev** — it's a thin wrapper around
  `docker build`/`docker run` (no separate daemon, no meaningful overhead on arm64), and
  using the same `docker-compose.yml` in both places avoids hand-retyping flags (volumes,
  device passthrough, env vars) when moving from laptop to cabin. The one difference: the
  Pi deployment should add `restart: unless-stopped` to the service so the container comes
  back up on its own after a power cycle in the truck; that's deliberately left off the dev
  compose file so a laptop container doesn't restart forever while you're iterating on it.

### Running it

```sh
cp .env.example .env   # then fill in MODEL_DRIVE_FILE_ID
docker compose up --build
```

`CAMERA_SOURCE` (env var) is passed straight to `cv2.VideoCapture`: `0` for the first
camera, a `/dev/videoN` path, or a video file path for testing without any camera attached.
The compose file passes through `/dev/video0` by default — adjust the `devices:` entry to
match your machine, or drop it entirely when testing against a video file.

## Model download strategy

The trained artifact is `lstm_geometric_feature_model_<VERSION>.keras` from the notebook's
"Saving the Geometric rate feature layer + LSTM Model" step, saved to the user's Google
Drive. It's fetched via `gdown` using a Drive file ID (`MODEL_DRIVE_FILE_ID` in `.env`),
which requires the file's sharing setting to be "Anyone with the link" (Viewer). This was
chosen over a Google Cloud service account for zero credential management — no GCP setup,
no secret to mount on the Pi — at the cost of the model file being link-accessible to
anyone who has the ID. Revisit this (service account + Drive API v3) if that trade-off
stops being acceptable.

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
├── model/          # GeometricRatioFeatureLayer + LstmGeometricFeatureModel defs, the gdown
│                   # downloader, and the predict(frame) -> (level, probabilities) wrapper
├── pipeline/       # MediaPipe FaceLandmarker wrapper + the camera capture loop that feeds it
├── orchestrator/   # decision logic: given a prediction (+ later, other signals like the
│                   # grip sensor), decides whether it's worth raising an alert at all
│                   # (thresholds, debounce/hysteresis across frames, cooldowns) — the
│                   # "Alert/RouteStatus Orchestration (Decision Making)" box in the design
│                   # diagram. Builds an Alert (via alerts/) and hands it to buffer/.
├── buffer/         # saving and queuing only: persists Alerts to SQLite (enqueue) and tracks
│                   # sent/unsent state — the "Queue Message Local Buffer (SQLite)" box in the
│                   # design diagram, and the answer to "what happens when the truck goes
│                   # offline". No communication logic of its own — sender/ dequeues from it.
├── sender/         # owns the actual communication: sets up whatever transport reaches the
│                   # ESP32 (or beyond), dequeues Alerts from buffer/ as connectivity allows,
│                   # transmits them, and reports back to buffer/ what succeeded so it can be
│                   # removed from the queue.
└── alerts/         # data model + serialization only for an Alert record (level,
                     # probabilities, timestamp, geolocation, ...) — no logic, no persistence;
                     # orchestrator/, buffer/, and sender/ all depend on this, not vice versa
```

Internal file names within each subpackage aren't decided yet — the notes below describe
required *behavior*, to carry forward regardless of how the files end up split up.

### `model/` — must match the notebook's classes exactly

`LstmGeometricFeatureModel` is a custom Keras `Model` subclass and `GeometricRatioFeatureLayer`
a custom `Layer` subclass. Loading a saved model that uses custom classes requires the
*exact same class definitions* to be importable at load time and passed as `custom_objects`
to `tf.keras.models.load_model()` — Keras can't reconstruct arbitrary custom `call()` logic
from the saved file alone. These classes must be ported verbatim from notebook cells 74 and
144 (search the notebook for `class GeometricRatioFeatureLayer` / `class
LstmGeometricFeatureModel`), including the `blendshape_names` class attribute (52 ARKit-style
names, set on `GeometricRatioFeatureLayer` in the notebook's "Initial Setup and Blendshape
Names" cell) and the `pose_validity_threshold_deg=20.0` default.

Key behavior to know when wiring the predict wrapper around it:

- **It's already stateful — don't build your own sequence buffer.** The model holds a
  `tf.Variable feature_buffer` of shape `(1, max_timesteps=60, num_features=59)` internally.
  Each call to the model shifts the buffer left by one frame and appends the new one, so
  `model/` should call it once per incoming frame, not accumulate a window itself.
- **Input** is a dict, not positional args:
  `{'landmarks': (1, 478, 2), 'rotation_matrix': (1, 3, 3), 'blendshapes': (1, 52)}` — raw
  MediaPipe outputs for a single frame, batch size fixed at 1.
- **Output** is a softmax over 6 classes (0-indexed; add 1 to get the drowsiness level 1–6),
  since the internal buffer already represents the trailing `max_timesteps` of history —
  there's no separate "wait until the window fills" step needed before predictions are
  meaningful, though early predictions (buffer still mostly zero-padded) will be less
  reliable until ~6 seconds of frames have been fed in.

### `pipeline/` — follow the notebook's live-simulation pattern, not `LIVE_STREAM` mode

The notebook downloads `face_landmarker.task` directly over HTTP (public URL, no auth —
see notebook cell "Downloading and Setting Up the MediaPipe Model Bundle") and configures
`FaceLandmarkerOptions` with `running_mode=vision.RunningMode.VIDEO`, `num_faces=1`,
detection/presence/tracking confidence thresholds of `0.5`, and both
`output_face_blendshapes` and `output_facial_transformation_matrixes` enabled. For live
camera frames, replicate the notebook's "End-to-End Live Stream Simulation" cell: keep using
`VIDEO` mode and call `detect_for_video(mp_image, timestamp_ms)` with a monotonically
increasing timestamp (wall-clock based, since there's no fixed source FPS from a live
camera) — rather than switching to MediaPipe's `LIVE_STREAM` callback mode, which is a
different API shape that was never validated in the notebook.

### `orchestrator/`, `buffer/`, `sender/`, `alerts/` — intentionally stubbed

None of these four are designed yet beyond the split of responsibility above. What's known so
far is the data flow: `orchestrator/` decides send-or-not → builds an `Alert` using
`alerts/`'s model → hands it to `buffer/`, which only saves and queues it (SQLite, tracks
sent/unsent) → `sender/` is what actually dequeues from `buffer/` and owns communication,
transmitting to the ESP32 described in `docs/designs/semantic-design*` (that hardware and
firmware don't exist in this repo) when connectivity allows, and reporting success back to
`buffer/` so the item can be removed from the queue. Build `sender/` against a small interface
(e.g. a `Transport.send(alert) -> bool`) with a logging/no-op implementation for now, so the
rest of the pipeline has somewhere to hand off predictions without prematurely committing to a
wire protocol that's still an open question in the system design (see the root `CLAUDE.md`'s
"Planned end-to-end system architecture" section).

## Open decisions that affect this module later

- Pi ↔ ESP32 transport (serial vs. Wi-Fi/MQTT) — decides whether `sender/`'s real `Transport`
  talks over `/dev/ttyAMA0`/`/dev/ttyUSB0` (device passthrough into the container) or a
  network socket (no device passthrough needed).
- Real camera on the Pi: settled as `picamera2` (see "Python packaging" above) for the CSI
  module, rather than raw `/dev/video*` + `libcamera` device passthrough — still worth a
  dedicated smoke test on real Pi hardware early, since `picamera2` inside a container has
  its own passthrough requirements (typically `/dev/video*` plus `/dev/dma_heap/*`) that
  haven't been verified in this repo yet. A dev laptop's USB webcam keeps using plain
  `cv2.VideoCapture` either way.

## Summary

Project setup for this module is done. The module breakdown is six pieces, with `buffer/`
and `sender/` cleanly split:

| Module | Responsibility |
|---|---|
| `model/` | LSTM/geometric-feature inference |
| `pipeline/` | camera + MediaPipe FaceLandmarker capture loop |
| `orchestrator/` | decides send-or-not, builds an `Alert` |
| `buffer/` | saving and queuing ONLY (SQLite, sent/unsent tracking) — no comms |
| `sender/` | owns communication setup, dequeues from `buffer/`, transmits, reports success back |
| `alerts/` | `Alert` data model + serialization only |

None of the six exist as code yet — what exists is the container (Dockerfile,
docker-compose.yml, two volumes for the model cache and the SQLite buffer), the packaging
(`setup.py` mapping `src/` to the `cv_argus` import name), and a real entry point
(`src/main.py` + `src/__main__.py`, run via `python -m cv_argus`, `python -m cv_argus.main`,
or the `cv-argus-run` console script) that currently just verifies the environment. Design
work on the six modules above continues outside this repo — pick up from this file rather
than re-deriving the plan from scratch.
