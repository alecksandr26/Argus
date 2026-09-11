# cv-argus

The Raspberry Pi 5 edge module: camera → MediaPipe → drowsiness-classification model, running
live in a Docker container. This is the practical "how do I run it" guide — for the
architecture, why things are built the way they are, and the deep notebook-fidelity contract,
see [`CLAUDE.md`](CLAUDE.md) instead.

## Quick start

```sh
cd src/cv-argus
docker compose up --build
```

That's it — **no `.env` file, no configuration, needed to get it building and running.** This
builds and runs the fused CNN-embedding + geometric-feature + LSTM pipeline (the only pipeline
this module runs — see `CLAUDE.md`'s "Current status") against whatever's at `/dev/video0`, and
logs each frame's drowsiness classification to the console. Both trained models' Google Drive
file IDs have checked-in defaults, so the build doesn't need one supplied.

To actually **watch** it work instead of reading log lines, see "Demo" below.

## Prerequisites

- Docker + Docker Compose (`docker compose`, the plugin form — not the old standalone
  `docker-compose`).
- A camera: a USB webcam (laptop) or the Pi 5's CSI camera (see "Demo" → "On the Pi 5" below) —
  or, with no camera at all, point `CAMERA_SOURCE` at a recorded video file instead (see
  "Configuration").
- Network access at build time, to download the pretrained MediaPipe bundles and the trained
  model weights (all baked into the image at `docker build`, not fetched later at container
  start — see `CLAUDE.md`'s "Why a Docker-first workflow" for why).

## What you'll see

With the demo output enabled (`OUTPUTS=logging,mjpeg`, see below), opening the stream URL in a
browser shows the live camera feed with the current classification drawn on it:

```
┌──────────────────────────┐
│  [ camera feed frame ]   │
│   ┌──────┐               │
│   │ face │  ← box around │
│   └──────┘    the detected face
│                          │
│  STATUS: Not Drowsy (green) │
│  fps: 11.2               │
└──────────────────────────┘
```

`STATUS` is color-coded — green for `Not Drowsy`, red for `Drowsy` — and comes straight from the
model, so it'll be as reliable (or not) as the model currently is; see `CLAUDE.md`'s "Current
status" for the honest caveat on that (real accuracy, but a single fold, not yet
cross-validated).

## Configuration

Everything below is an environment variable — set inline (`VAR=value docker compose up
--build`), or in a `.env` file (`cp .env.example .env` first; see that file for the full list
including model-artifact overrides not covered here).

| Variable | Default | What it does |
|---|---|---|
| `CAMERA_SOURCE` | `0` | Passed to `cv2.VideoCapture`: an integer camera index, a `/dev/videoN` path, or a video file path (for testing/demoing with no camera attached). |
| `SOURCE` | `video_capture` | Where frames come from: `video_capture` (`cv2.VideoCapture`, driven by `CAMERA_SOURCE`) or `picamera` (the Pi 5's CSI camera — see "On the Pi 5" below). |
| `OUTPUTS` | `logging` | Comma-separated sink(s): `logging` (text only) and/or `mjpeg` (the browser-viewable demo stream — e.g. `OUTPUTS=logging,mjpeg`). |
| `SAMPLE_FPS` | `5` | Frames/sec sampled from the camera, decimated at the source (skipped frames aren't decoded). `5` matches the model's training rate; `0` = every frame. See "Simulating the Raspberry Pi 5" below. |
| `DEMO_STREAM_PORT` | `8080` | Only read when `OUTPUTS` includes `mjpeg`. Change if `8080` is already taken on your machine. |
| `LATENCY_LOG_INTERVAL` | `10` | Seconds between per-stage latency / queue-depth / dropped-frame report lines (see "Finding the bottleneck" below). `0` disables them. |
| `LOG_LEVEL` | `INFO` | Root log level. `DEBUG` also prints a line per processed frame and the drop-oldest debug logs. |
| `CV_ARGUS_NUM_THREADS` | `4` (in Docker) | Pins BLAS/OpenMP/TensorFlow/OpenCV thread pools. Unset outside Docker (full speed). See "Simulating the Raspberry Pi 5". |
| `CV_ARGUS_CPUSET` / `CV_ARGUS_CPUS` / `CV_ARGUS_MEM` | `0-3` / `4` / `8g` | Container cgroup limits — the 4-core / 8 GB Pi 5 ceiling. Docker-only. |

There's one pipeline — the frozen-CNN-embedding + geometric-feature + LSTM classifier — see
`CLAUDE.md`'s "Current status" for its measured accuracy and caveats.

**`mjpeg` has no authentication.** It's meant for demos on a network you trust, not for leaving
on — see `CLAUDE.md`'s "Demo" section for why this matters more than usual for this project.

## Running it without Docker (local dev)

Docker is the supported way to run this (same image on a laptop and the Pi — see `CLAUDE.md`'s
"Why a Docker-first workflow"). But for quick iteration you can run it straight from a
virtualenv:

```sh
cd src/cv-argus
python -m venv .venv && . .venv/bin/activate
pip install -e .                 # installs mediapipe / tensorflow / opencv per setup.py
MODEL_DIR=./models python -m cv_argus     # <- runs the pipeline; same entry point as the container
```

Other equivalent entry points: `python -m cv_argus.main`, or the `cv-argus-run` console
script that `pip install` puts on your PATH.

`MODEL_DIR` defaults to `/app/models` (the container path) — **set it to a writable local
directory** when running outside Docker. The **trained model + MediaPipe bundles download on
first run** into `MODEL_DIR` and are reused after that (needs network the first time, ~a few
hundred MB); the Docker build bakes them into the image instead. Set the rest of the env vars
inline the same way:

```sh
MODEL_DIR=./models CAMERA_SOURCE=~/clip.mp4 OUTPUTS=logging,mjpeg LATENCY_LOG_INTERVAL=5 python -m cv_argus
```

## Simulating the Raspberry Pi 5

The truck-cabin target is a **Raspberry Pi 5** (4× Cortex-A76, no dGPU). `docker compose up`
holds the container to that envelope **by default**, so the latency numbers you see on a beefy
dev box actually mean something:

- **`cpuset: 0-3` + `cpus: 4`** — pins threads to 4 CPUs *and* caps total CPU-time at 4 cores.
  MediaPipe's XNNPACK pool has no Python thread knob, so this cgroup limit is the only thing
  that constrains it.
- **`mem_limit: 8g`** — the 8 GB board (actual use is ~400 MB, so this only catches leaks).
- **`CV_ARGUS_NUM_THREADS=4`** — pins the BLAS / OpenMP / TensorFlow / OpenCV pools so they
  don't oversubscribe within that ceiling (`src/bootstrap.py`).
- **`SAMPLE_FPS=5`** — the model's training rate; also the single biggest lever for staying
  within a Pi's budget.

For an **unthrottled local run**, widen them in `.env` or inline:

```sh
CV_ARGUS_CPUSET=0-11 CV_ARGUS_CPUS=12 CV_ARGUS_NUM_THREADS=12 CV_ARGUS_MEM=32g docker compose up
```

On a real Pi 5 the defaults are already the hardware's actual limits — the overlay
(`docker-compose.pi.yml`) changes nothing here.

Check it's applied:

```sh
docker compose exec cv-argus python -c "import os; print(len(os.sched_getaffinity(0)), 'cpus')"
docker compose exec cv-argus python -c "import os; print(os.environ['OMP_NUM_THREADS'], 'threads')"
```

## Running it as a service (the truck cabin)

On the Pi the container is meant to come back up on its own after a power cycle — that's what
the `docker-compose.pi.yml` overlay's `restart: unless-stopped` is for (see "On the Raspberry
Pi 5" below). The dev `docker-compose.yml` deliberately leaves that off so a laptop container
doesn't restart forever while you're iterating.

**Lifecycle:** the process runs until it gets `SIGINT` (Ctrl-C) or `SIGTERM` (`docker compose
down` / `docker stop`), at which point it shuts every pipeline stage down in order and exits
cleanly. A **video-file** `CAMERA_SOURCE` also makes it exit on its own when the file ends;
a live camera runs until stopped.

## Demo: watching it work

### On a laptop

```sh
OUTPUTS=logging,mjpeg docker compose up --build
```

Then open **`http://localhost:8080/stream`** in a browser. If your webcam isn't at
`/dev/video0`, edit `docker-compose.yml`'s `devices:` entry to match, or drop it and set
`CAMERA_SOURCE` to a recorded video file path instead — a good, zero-hardware-risk fallback if
you'd rather not depend on a live camera and decent lighting for a demo.

### On the Raspberry Pi 5

Uses the CSI camera via a separate overlay file that adds the right device passthrough,
`SOURCE=picamera`, and `restart: unless-stopped` on top of the base compose file:

```sh
OUTPUTS=logging,mjpeg docker compose -f docker-compose.yml -f docker-compose.pi.yml up --build
```

Then open **`http://<pi-ip>:8080/stream`** from any device's browser on the same network (find
the Pi's IP with `hostname -I` on the Pi itself).

**Test this path well before you actually need it for something important.** The device
passthrough in `docker-compose.pi.yml` is a best-effort list, not verified against real Pi 5
hardware yet — if the camera doesn't show up, run `ls /dev/video*` and `ls /dev/dma_heap/` on
the Pi itself and compare against that file's `devices:` list; it may need adjusting. CPU
performance for this model on a Pi 5 is also unmeasured — if it feels slow, that's expected to
be checked, not a sign something's broken.

## Finding the bottleneck

Every stage logs a timing summary every `LATENCY_LOG_INTERVAL` seconds (default 10). Watch
them with `docker compose logs -f` (or straight to the console with `docker compose up`):

```
stats video_capture.produce: n=300 proc(mean=3.2ms p50=3 p95=6 max=12) drop=250 life(n=1500 max=33ms)
stats face_detector_crop.process: n=48 wait(mean=33ms p95=40 max=41) proc(mean=61.3ms p50=58 p95=98 max=140) inq(avg=3.8 max=4) drop=0 life(...)
stats face_landmarker_crop.process: n=48 wait(mean=0.2ms ...) proc(mean=22.1ms p50=21 p95=30 max=44) inq(avg=0.2 max=2) drop=0 life(...)
stats fused_inference.process: n=48 wait(mean=0.1ms ...) proc(mean=41.0ms p50=39 p95=63 max=90) embed(mean=27.0ms ...) lstm(mean=13.5ms ...) inq(avg=1.1 max=4) drop=0 life(...)
stats logging_output.process: n=48 wait(mean=0.1ms ...) proc(mean=0.1ms ...) e2e(mean=520ms p95=690) drop=0 life(...)
```

A frame's whole trip, and which number is which:

```
video_capture.produce  proc = grabbing one frame (cap.read)
   │  queue
face_detector_crop      wait = time queued   proc = MediaPipe BlazeFace + crop
   │  queue
face_landmarker_crop    wait = time queued   proc = MediaPipe FaceLandmarker + geo features
   │  queue
fused_inference         wait = time queued   proc = model  → embed = frozen CNN, lstm = sequence model
   │  queue
logging_output          wait = time queued   proc ≈ 0       e2e = grab-to-here (the whole trip)
```

- **`proc(mean=...)`** — work done *inside* the stage. The **largest one is the compute
  bottleneck** (here `face_detector_crop` at ~61 ms → the pipeline can't exceed ~16 fps).
- **`wait(mean=...)`** — how long the frame sat in that stage's input queue first. The stage
  with the **biggest `wait`** is where frames pile up (it's right in front of the bottleneck).
  All the `wait`s + all the `proc`s ≈ the sink's `e2e`; the rest is scheduling.
- **`embed` / `lstm`** on `fused_inference` — that stage's `proc` split into the frozen CNN
  embed vs. the LSTM predict, so you can tell which half of the model to worry about. Both run
  through a traced `tf.function` (see `CLAUDE.md`'s fused-detector section), so expect a few ms
  each; `lstm` in the hundreds of ms means the graph is re-tracing every frame — a regression.
- **`inq(avg=.. max=N)`** — input-queue depth (capacity 4). A stage **pinned at `max=4`** is
  the bottleneck or right behind it.
- **`drop=`** — frames decoded and emitted but that a full downstream queue forced the source
  to shed. With `SAMPLE_FPS=5` (the default) this should sit at ~0 — the ~25/s of *skipped*
  camera frames are `grab()`-discarded before decode and never counted here. A climbing `drop`
  means the pipeline can't keep up even at 5 fps. (With `SAMPLE_FPS=0` it's back to shedding
  most of a 30 fps feed, and `drop` is large and expected.)
- **`e2e(mean=...)`** — capture-to-output latency at the sink. The "it feels laggy" number.
- source `.produce` line: `n / LATENCY_LOG_INTERVAL` ≈ the **sampled** fps (should track
  `SAMPLE_FPS`); its `proc` is the per-kept-frame decode cost, not the full camera rate.

`LATENCY_LOG_INTERVAL=0` turns all of this off.

## Troubleshooting

- **`docker compose build` fails immediately, no network-related error** — check you're running
  the plugin form (`docker compose`, two words) and not the deprecated standalone
  `docker-compose` binary.
- **On WSL2: `docker` command not found, or "daemon not reachable"** — Docker Desktop's WSL
  integration usually needs to be turned on per-distro: Docker Desktop → Settings → Resources →
  WSL Integration → enable it for whichever distro you're running this from, then restart your
  terminal.
- **On WSL2: `docker compose up` fails with `error gathering device information while adding
  custom device "/dev/video0": no such file or directory`** — under Docker Desktop's WSL2
  backend, the Docker "host" is the WSL2 Linux VM, not Windows, and WSL2 doesn't expose Windows'
  USB devices (a USB webcam included) into its Linux kernel by default — `/dev/video0` genuinely
  doesn't exist there until the device is explicitly attached. Fix it with
  [`usbipd-win`](https://github.com/dorssel/usbipd-win) (Microsoft's official USB/IP tool), run
  from an **elevated (Administrator) Windows PowerShell**, not from inside WSL2:
  ```powershell
  winget install usbipd        # one-time
  usbipd list                  # find your webcam's BUSID
  usbipd bind --busid <BUSID>  # one-time per device, persists across reboots
  usbipd attach --wsl --busid <BUSID>   # needed again after every reboot / USB replug / `wsl --shutdown`
  ```
  Then confirm it showed up from inside WSL2 with `ls /dev/video*` before retrying
  `docker compose up`. If the webcam lands on an index other than 0, update
  `docker-compose.yml`'s `devices:` entry to match. If you'd rather skip USB passthrough
  entirely, use the video-file fallback instead (see "Demo" above) — no Windows-side steps
  needed.
- **Build fails trying to reach Google/Drive** — the build needs real network access to fetch
  the MediaPipe bundles and the trained model weights (see "Prerequisites"); this isn't optional
  the way it might be for a project with a bundled/offline fallback.
- **Camera opens but no face is ever detected** — check lighting and that the camera is actually
  pointed at a face; also confirm `CAMERA_SOURCE`/`SOURCE` actually point at the device you think
  they do (`docker compose logs` will show `cv-argus starting (SOURCE=..., OUTPUTS=...,
  LATENCY_LOG_INTERVAL=...)` on startup, confirming what it's actually using).
- **The `mjpeg` stream shows a solid green/corrupted image instead of the camera feed** — the
  camera opened fine (`cap.read()` reports success) but the negotiated pixel format is wrong;
  this is the single most common cause of "no face ever detected" too, since MediaPipe is
  correctly finding zero faces in a frame with no real picture in it. `VideoCaptureSource`
  already forces MJPG on any live camera to fix this (see `pipeline/sources.py`), but a webcam
  passed through `usbipd-win` into WSL2 is the case most likely to still hit it — reattach the
  device (`usbipd attach --wsl --busid <BUSID>` again) and retry before assuming the fix didn't
  work.
- **`http://localhost:8080/stream` doesn't load** — confirm `OUTPUTS` actually includes `mjpeg`
  (the default is `logging` only, which produces no stream at all — check `docker compose logs`
  for a `"serving MJPEG stream at http://..."` line to confirm it started), and that nothing
  else on your machine is already using port `8080` (set `DEMO_STREAM_PORT` to something else if
  so).
- **A newly trained model doesn't seem to be picked up after rebuilding** — Docker only seeds a
  named volume (`model-cache`) from the image the *first* time it's created; a rebuild alone
  doesn't refresh an existing one. Run `docker compose down -v` first — see `CLAUDE.md`'s "Model
  download strategy" → "Gotcha this creates" for the full explanation.

## Running the tests

```sh
cd src/cv-argus
pip install -e .          # once -- maps src/ to the cv_argus import name (see setup.py)
pytest                    # the full unit suite
```

The default run is **hermetic** — no network, no camera, no Google Drive, no model download.
Every downloader test either exercises the skip-if-cached path or monkeypatches `gdown` /
`urllib`. It needs `tensorflow` + `mediapipe` + `opencv` installed (the same deps the app
needs); `pip install -e .` pulls them in.
`tests/` mirrors `src/`: `test_model_*`, `test_pipeline_*` (including `test_pipeline_latency.py`
for the `StageStats` instrumentation), and `test_main.py`. `FusedDrowsinessDetector` is tested
with **stub** model callables — the real `.keras` files are never loaded in the unit suite.
There is a second, opt-in tier that actually builds and runs the Docker image:

```sh
CV_ARGUS_DOCKER_TESTS=1 pytest -m docker
```

It is deselected from a plain `pytest` and additionally skips itself unless `docker` is on
`PATH` and `CV_ARGUS_DOCKER_TESTS=1` — the build pulls ~1GB of TensorFlow plus the model
artifacts and takes minutes. It checks the image builds, `import cv_argus` works inside it,
the four model artifacts are baked into `/app/models`, re-running the downloaders is a cached
no-op, and the `SOURCE`/`OUTPUTS` guards reject bad values.

## Where to go next

- [`CLAUDE.md`](CLAUDE.md) — the real architecture: the `Stage`/`Pipeline` threading design, the
  fused pipeline's measured accuracy and open caveats, exact model input/output shapes, and
  every convention worth knowing before changing code here.
- [`tests/`](tests/) — the `pytest` suite (mirrors `src/`: `test_model_*`, `test_pipeline_*`,
  `test_main.py`, plus the opt-in `test_docker_build.py`).
- [`scripts/smoke_test_pipeline.py`](scripts/smoke_test_pipeline.py) — a synthetic test of the
  threading/queue plumbing itself, runnable with no camera, no model, and none of `cv2`/
  `mediapipe`/`tensorflow` installed. `tests/test_pipeline_stage.py` is the maintained `pytest`
  version of the same checks.
