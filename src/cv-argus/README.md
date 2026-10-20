# cv-argus

The Raspberry Pi 5 edge module: camera → MediaPipe BlazeFace → our tiny YOLO eye/mouth/head
detector → a tracker and timed rules that decide a severity → an alert-decision loop that queues Alerts/RouteStatus heartbeats locally (SQLite) for a Bluetooth
hand-off to the ESP32 — running live in a Docker container. This is the practical "how do I run
it" guide — for the architecture, why things are built the way they are, and the deep
notebook-fidelity contract, see [`CLAUDE.md`](CLAUDE.md) instead.

## Quick start

```sh
cd src/cv-argus
docker compose up --build
```

That's it — **no `.env` file, no configuration, needed to get it building and running.** This
builds and runs the parts pipeline (tiny YOLO + tracker + rules, the only pipeline this module
runs) against whatever's at `/dev/video0`, and logs one line per frame: the severity, the eye /
mouth / head states and the inference time. The model's Google Drive file IDs have checked-in
defaults, so the build doesn't need one supplied. (The older CNN+LSTM pipeline is on branch
`archive/cnn-lstm-fused`.)

To actually **watch** it work instead of reading log lines, run `scripts/demo.sh` (see "Demo"
below).

### Building vs. running — you don't need `--build` every time

`--build` does two things: builds the image (or rebuilds it, if anything relevant changed) and
then starts the container from it. **You only need it the first time, or after a change that
needs a rebuild** (edits to `Dockerfile`, `requirements.txt`/`setup.py`, or a
`PARTS_INT8_DRIVE_FILE_ID`/`PARTS_FLOAT_DRIVE_FILE_ID` build arg — see `docker-compose.yml`'s
comment at the top). Once `argus/cv-argus:dev` already exists (`docker images` will show it),
just run:

```sh
docker compose up          # starts the existing image, no rebuild
```

Code under `src/` doesn't even need a rebuild to pick up edits — it's bind-mounted into the
container (see the comment at the top of `docker-compose.yml`), so `docker compose up` alone is
enough for day-to-day iteration on `cv_argus`'s Python code. Reach for `--build` again only when
one of the rebuild triggers above actually applies; running it unconditionally just costs time
re-checking Docker's build cache for no benefit. Add `-d` to either form to run detached
(`docker compose logs -f` to reattach), and `docker compose down` to stop and remove the
container (`down -v` also wipes the named volumes — see "Model download strategy" in
`CLAUDE.md` for when that's actually needed).

## Prerequisites

- Docker + Docker Compose (`docker compose`, the plugin form — not the old standalone
  `docker-compose`).
- A camera: a USB webcam (laptop) or the Pi 5's CSI camera (see "Demo" → "On the Pi 5" below) —
  or, with no camera at all, point `CAMERA_SOURCE` at a recorded video file instead (see
  "Configuration").
- Network access at build time, to download the pretrained MediaPipe bundles and the trained
  model weights (all baked into the image at `docker build`, not fetched later at container
  start — see `CLAUDE.md`'s "Why a Docker-first workflow" for why).
- **A Bluetooth adapter, only if you want the ESP32 link running.** The container can't open
  Bluetooth sockets (rootless Docker), so `docker-compose.yml` defaults `SENDER_TRANSPORT=none`:
  alerts and RouteStatus heartbeats still get decided and queued in `buffer/`'s SQLite file.
  The server the ESP32 polls runs on the Pi host instead (`scripts/pi_bt_server.py`, or
  `scripts/demo.sh --bluetooth`) — see "Bluetooth link test". Nothing extra is needed to run
  the quick start below as-is.

## What you'll see

With the demo output enabled (`scripts/demo.sh`, or `OUTPUTS=logging,mjpeg`), opening the
stream URL in a browser shows the live camera feed with:
- each detected part boxed and labelled `ID class confidence` (E = eye, M = mouth, H = head);
- a severity banner (`CRITICAL  eyes closed 3.2 s`);
- timer bars for eyes closed, head down and mouth open;
- a footer with precision, thresholds, model ms and fps.

See "Demo: the parts pipeline" below for the details and the severity table.

## Configuration

Everything below is an environment variable — set inline (`VAR=value docker compose up
--build`), or in a `.env` file (`cp .env.example .env` first; see that file for the full list
including model-artifact overrides not covered here).

| Variable | Default | What it does |
|---|---|---|
| `PARTS_PRECISION` | `int8` | `int8` (`.tflite`, ~0.7 ms/face) or `float` (`.keras`, ~5.5 ms/face): the same detector at two precisions. |
| `PARTS_INT8_FALLBACK` | `1` | If the int8 model can't load or run on this device (it has failed on a Pi 5), use the float `.keras` instead. Checked at startup in a child process; `0` = fail instead. |
| `PARTS_CLOSED_EYE_THRESHOLD` | `0.40` | An eye counts as closed when p_closed / (p_open + p_closed) ≥ this. 0.5 = plain argmax; lower = more sensitive. |
| `PARTS_HEAD_DOWN_THRESHOLD` | `0.30` | Same for head down vs. straight. Lower than the eye one because `head_down` is the detector's weakest class. |
| `RULE_*` | see `src/constants.py` | Rule timers in seconds, e.g. `RULE_EYES_CRITICAL_S=3.0`, `RULE_YAWN_RECENT_S=10`, `RULE_HEAD_GRACE_S=1.0`. |
| `METRICS_LOG_PATH` | `$BUFFER_DIR/metrics.jsonl` | Where `OUTPUTS=metrics` writes one JSON line per frame (see "Measuring it for the report"). |
| `RESOURCE_SAMPLE_S` | `1` | How often the CPU-per-core / RAM reading in the logs and metrics is re-sampled, in seconds. |
| `DEMO_STREAM_WIDTH` | unset | Downscale the MJPEG stream to this width (boxes are drawn first, so they stay aligned). |
| `CAMERA_SOURCE` | `0` | Passed to `cv2.VideoCapture`: an integer camera index, a `/dev/videoN` path, or a video file path (for testing/demoing with no camera attached). |
| `SOURCE` | `video_capture` | Where frames come from: `video_capture` (`cv2.VideoCapture`, driven by `CAMERA_SOURCE`) or `picamera` (the Pi 5's CSI camera — see "On the Pi 5" below). |
| `OUTPUTS` | `logging` | Comma-separated sink(s): `logging` (one text line per frame), `metrics` (one JSON line per frame with every timing) and/or `mjpeg` (the browser-viewable demo stream — e.g. `OUTPUTS=logging,metrics,mjpeg`). |
| `SAMPLE_FPS` | `5` | Frames/sec sampled from the camera, decimated at the source (skipped frames aren't decoded). `5` is the rate the tracker and rule timers are tuned for; `0` = every frame. See "Simulating the Raspberry Pi 5" below. |
| `DEMO_STREAM_PORT` | `8080` | Only read when `OUTPUTS` includes `mjpeg`. Change if `8080` is already taken on your machine. |
| `LATENCY_LOG_INTERVAL` | `10` | Seconds between per-stage latency / queue-depth / dropped-frame report lines (see "Finding the bottleneck" below). `0` disables them. |
| `LOG_LEVEL` | `INFO` | Root log level. `DEBUG` also prints the drop-oldest debug logs. |
| `CV_ARGUS_NUM_THREADS` | `4` (in Docker) | Pins BLAS/OpenMP/TensorFlow/OpenCV thread pools. Unset outside Docker (full speed). See "Simulating the Raspberry Pi 5". |
| `CV_ARGUS_CPUSET` / `CV_ARGUS_CPUS` / `CV_ARGUS_MEM` | `0-3` / `4` / `8g` | Container cgroup limits — the 4-core / 8 GB Pi 5 ceiling. Docker-only. |
| `BUFFER_DIR` / `BUFFER_DB_FILENAME` | `/app/data` / `buffer.sqlite3` | Where `buffer/`'s SQLite alert queue lives — matches the `buffer-data` volume. |
| `SENDER_TRANSPORT` | `none` (this file) / `bluetooth` (main.py's own default) | `bluetooth` (real `BluetoothSppListener` — needs a real adapter + device passthrough, see "Prerequisites") or `none` (no `SenderServer`; alerts just accumulate unsent in `buffer/`). |
| `BLUETOOTH_CHANNEL` | `4` | Only read when `SENDER_TRANSPORT=bluetooth`: the RFCOMM channel `BluetoothSppListener` binds. |
| `BLUETOOTH_SECURITY` | `medium` | RFCOMM security the ESP32's link must meet: `medium` = paired + encrypted (required: `CONFIG` sends the device key), `high` = also MITM-protected pairing (an ESP32 without display/keypad can't do it), `low` = anything in range, bench only. |
| `ESP32_BT_MAC` | unset | Optional comma-separated MAC allowlist (this truck's ESP32). Unset = any paired device. |
| `ARGUS_TRUCK_ID` / `ARGUS_DEVICE_API_KEY` / `ARGUS_BACKEND_URL` | unset | This truck's backend credentials, from `argus-device.env` — see "Provisioning a truck" below. All three or none; a partial set stops startup. |
| `ARGUS_WIFI_SSID` / `ARGUS_WIFI_PASSWORD` | unset | The WiFi the ESP32 falls back to (phone hotspot / 4G MiFi, 2.4 GHz). Set in `.env`, sent in `CONFIG`; used verbatim (spaces included). Unset = the ESP32's WiFi stays off. |
| `ARGUS_GPRS_APN` / `ARGUS_GPRS_USER` / `ARGUS_GPRS_PASSWORD` | unset | The SIM's mobile-data APN. Set in `.env`, sent in `CONFIG`. Set = the ESP32 uses mobile data first, WiFi second; unset = WiFi only. Carrier values in `.env.example`. |

## Provisioning a truck

The Pi holds the truck's backend credentials; the ESP32 fetches them from it over Bluetooth and
is the one that actually calls the backend (one firmware image fits every truck).

1. **Dashboard**: Fleet → pick the truck → **Generate device credentials** (root_admin/admin).
   Check the "Backend URL the truck uses" field (the AWS `https://…cloudfront.net` URL, or a
   LAN IP for a bench backend — never `localhost`), then **Download argus-device.env**. The key
   is shown once; lost it → **Rotate key** and download again (the old one stops working).
2. **Pi**: copy that file next to this `docker-compose.yml` as `argus-device.env` (it's
   gitignored and dockerignored: loaded at runtime only, never baked into the image), then
   `docker compose up -d`. The log shows `Device credentials loaded for truck <id>` (never the
   key).
   **The ESP32's network goes in this directory's `.env`** (not in `argus-device.env`, which
   the dashboard regenerates on every key rotation): `ARGUS_WIFI_SSID`/`ARGUS_WIFI_PASSWORD`
   for WiFi and/or `ARGUS_GPRS_APN` (+ `_USER`/`_PASSWORD`) for the SIM's mobile data — see
   `.env.example`. The ESP32 gets them in the same `CONFIG` reply, so nothing about the network
   is flashed into its firmware either; after changing them, `docker compose up -d` and the
   ESP32 picks them up within 10 minutes.
3. **Pair the ESP32 once** on the Pi host, since the link must be encrypted
   (`BLUETOOTH_SECURITY=medium`):
   ```sh
   bluetoothctl
   > power on
   > agent NoInputNoOutput
   > default-agent
   > pairable on
   # power the ESP32; it pairs itself (SSP "Just Works") on its first connect
   > trust AA:BB:CC:DD:EE:FF      # the ESP32's MAC; optionally also set ESP32_BT_MAC to it
   ```
   Not yet run on the real Pi + ESP32 pair: if `bluetoothctl` asks to confirm a passkey or
   authorize a service, answer `yes`. `BLUETOOTH_SECURITY=low` skips this for a quick bench
   test, but sends the key unencrypted.
4. The ESP32 asks for `CONFIG` at boot, after any `401` and every 10 minutes, caches it, and finds its route with
   `GET /api/devices/me/active-route`. The Fleet screen's Devices column shows "Key · seen …"
   once it's reporting.

The container itself can't serve Bluetooth (see "Prerequisites"), so `SENDER_TRANSPORT=none`
keeps it starting. The ESP32 fetches these credentials from `scripts/pi_bt_server.py` on the Pi
host instead, which reads `argus-device.env` and `.env` the same way — see "Bluetooth link
test" below.

Downstream of a detection, `orchestrator/` reports the debounced class as a *reading* on every
change, plus a keepalive, and emits a periodic RouteStatus("OK") heartbeat, `buffer/` persists both to SQLite, and `sender/` answers
the ESP32's Bluetooth poll for unsent records — see `CLAUDE.md`'s "`orchestrator/`, `buffer/`,
`sender/`, `alerts/` — done" for the full design.

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
- **`SAMPLE_FPS=5`** — the rate the tracker and rule timers are tuned for; also the single biggest lever for staying
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

## Demo: the parts pipeline (tiny YOLO + rules)

The pipeline is simple and explainable:

1. BlazeFace finds the face, which is cropped square and converted to gray, exactly as in
   training.
2. Our own **46k-parameter detector** (notebook 12, `tiny_yolo_w1`) finds open/closed eyes, a
   yawning mouth, and head straight or down.
3. A tracker gives each part a stable ID.
4. **Deterministic rules with timers** decide the severity.

```sh
scripts/demo.sh                                   # int8, webcam 0 -> http://localhost:8080/stream
scripts/demo.sh --precision float                 # same detector as float .keras (does int8 lose accuracy?)
scripts/demo.sh --camera 1                        # another webcam (or --camera /dev/video2)
scripts/demo.sh --camera src/demo_clips/x.mp4     # a recorded clip (keep clips under src/demo_clips/, gitignored)
scripts/demo.sh --camera picam                    # Pi 5 CSI camera (adds docker-compose.pi.yml + this host's camera nodes)
scripts/demo.sh --eye-threshold 0.35 --head-threshold 0.25   # more sensitive closed / down
```

`demo.sh` turns on all three outputs: the console line per frame, the metrics file and the
stream.

What the stream shows:
- The face box, the dashed square the model saw, and each tracked part as `ID class confidence`
  (E = eye, M = mouth, H = head). Boxes are remapped from the 160 px crop onto the frame.
- A **severity banner** with the reason.
- **Timer bars** for *eyes closed*, *head down* and *mouth open*, with ticks at the
  LOW / MEDIUM / CRITICAL thresholds.
- A footer with precision, the closed-eye / head-down thresholds, model ms and fps.

**Two kinds of threshold.** `PARTS_CONF_THRESHOLD` (0.25) decides whether a part is detected at
all. The decision thresholds decide *which* state it's in:
- **Closed eye (0.40):** an eye counts as closed when p_closed / (p_open + p_closed) ≥ 0.40.
- **Head down (0.30):** the same test, for head down vs. straight.

0.5 is the model's plain argmax, and lower is more sensitive. On the labelled validation and test
faces, closed-eye recall is about 93% and barely moves with the threshold: the model is usually
sure either way. Head-down recall is only 52–69% per frame. To compensate:
- a head counts as down if **1 of its last 3 frames** was;
- the head timer tolerates 1 s gaps;
- straight heads are almost never misread as down.

More head-down training data is the real fix.

**Severity rules** (seconds; each is a `RULE_*` env var, overridable in `.env`):

| Situation | LOW | MEDIUM | CRITICAL |
|---|---|---|---|
| Eyes closed | 1.0 | 2.0 | **3.0** |
| Eyes closed, mouth open now or a yawn in the last 10 s | 0.7 | 1.3 | **2.0** |
| Head down + eyes closed or hidden | | | **3.0** (2.0 after a yawn) |
| Head down, eyes open (looking down) | | 3.0 | |
| Yawning alone: ≥ 3 yawns (≥ 1.5 s each) in 5 min | yes | never | never |
| No face for 3 s after one was seen | yes | | |

Closed eyes and head down are the risks; yawning only makes those fire sooner. Severity rises
immediately and drops only after 2 s clear. A one-frame flicker (≤ 0.4 s; 1 s for the head)
doesn't reset a timer.

### Measuring it for the report

`OUTPUTS=metrics` (on in `demo.sh`) writes one JSON line per frame. Each line has:
- the severity, reason and trigger time;
- the eye / mouth / head states and every tracked part with its confidence;
- the timers;
- the time of every step, in ms: BlazeFace, crop, model inference, decode, tracker, rules, the
  whole stage, the queueing between stages, and end to end;
- CPU and RAM (`res`), re-sampled every `RESOURCE_SAMPLE_S` (1 s):
  - CPU: busy % of each core and the average over all cores, plus cv-argus's own CPU as % of
    one core;
  - RAM: cv-argus's memory and the board's RAM in use.

The console shows the same thing as one line per frame, led by the criticality:

```
[pi_camera] criticality=LOW (no_face 13.6s) | face=no | eyes=- mouth=- head=- | eyes_closed 0.0s head_down 0.0s | ms blazeface 6.8 crop 0.6 infer 0.7 decode 0.3 track 0.1 rules 0.0 queue 0.3 e2e 7.2 | cpu 23% [12 30 25 26] proc 45% | ram 412MB sys 18%
```

```sh
docker compose cp cv-argus:/app/data/metrics.jsonl .
python3 scripts/summarize_metrics.py metrics.jsonl            # tables
python3 scripts/summarize_metrics.py metrics.jsonl --csv run  # + run_latency/_states/_resources.csv
```

The summary has:
- mean / p50 / p95 / p99 / max per step, including queueing;
- the share of frames in each state and severity;
- how many MEDIUM/CRITICAL episodes fired;
- CPU (board, each core, cv-argus) and RAM mean / p95 / max over the run. Example, int8 in Docker under the
4-core Pi limit on a desktop CPU, 127 frames of a face clip:

| step | mean | p95 |
|---|---|---|
| BlazeFace | 2.20 ms | 3.19 ms |
| model inference (int8) | 0.73 ms | 0.96 ms |
| whole parts stage | 1.85 ms | 2.45 ms |
| end to end | 14.8 ms | 17.2 ms |

**Measured on a desktop CPU** (real models, a 128-frame face clip):

| | int8 | float |
|---|---|---|
| Model per face | **0.6 ms** | 5.5 ms |
| Whole parts stage | 1.5 ms | |
| BlazeFace | about 2 ms | |
| Agreement with the other precision | same severity on 98.4% of frames | |

int8 is the default. The Pi 5 is slower and **unmeasured**.

The ESP32 and backend are unchanged:
- MEDIUM and CRITICAL go out as `Drowsy` (`p_drowsy` 0.75 / 0.95); none and LOW go out as
  `Not Drowsy`.
- `severity`, `reason`, `trigger_s` and `timers` ride along in the buffered reading's payload.

## Bluetooth link test: the Pi (server) ↔ the ESP32 (client)

Goal: watch a CRITICAL from the camera arrive on the ESP32's Serial Monitor. No backend, WiFi,
SIM or GPS involved.

The Pi is the **server** and the ESP32 the **client**. Every poll, the ESP32 opens a Bluetooth
connection to the Pi (classic SPP, RFCOMM channel 4), sends `PULL`, gets the queued readings,
and `ACK`s them. The Pi never pushes.

The Bluetooth server runs **on the Pi host**, not in the container: `scripts/pi_bt_server.py`,
plain `python3`, nothing to install. Linux only creates Bluetooth sockets in the host's own
network namespace, and a rootless-Docker container can't reach it. The container keeps the camera
pipeline and shares the alert queue with the host through `./data` (`docker-compose.bt.yml`).

**1. ESP32: flash the link-test build.** In `src/esp32-argus/src/esp32_argus/config.h` set:
- `PI_BT_MAC_ADDRESS` to the Pi's MAC (step 2 prints it in exactly this form);
- `BT_LINK_TEST_MODE 1`;
- `PI_BT_RFCOMM_CHANNEL 4` (the default).

Then flash it and open the Serial Monitor at 115200 baud. It prints its own Bluetooth MAC at
boot (`[bt] This ESP32's Bluetooth MAC: ...`).

**2. Pi: pair once, then start the server.** The ESP32 always asks for an encrypted, paired
link, so pair it first. Keep this `bluetoothctl` window open during the first connection:
```sh
bluetoothctl
> power on
> agent NoInputNoOutput
> default-agent
> pairable on
# reset the ESP32: it pairs itself (SSP "Just Works") on its first connect.
# If bluetoothctl asks to confirm a passkey or authorize a service, answer yes.
> trust AA:BB:CC:DD:EE:FF      # the ESP32's MAC from its Serial Monitor
> devices Paired               # should list ESP32-Argus
```
In a second terminal, from `src/cv-argus/`:
```sh
python3 scripts/pi_bt_server.py
```
It prints the Pi's adapter MAC as a `config.h` line, the paired devices, and then
`Listening on RFCOMM channel 4 ... Waiting for the ESP32...`.

**3. Prove the link with a fake critical**, before involving the camera. In a third terminal:
```sh
python3 scripts/pi_bt_server.py --inject critical
```
What you should see:
- **Pi:**
  - `sender: ESP32 AA:BB:... connected over Bluetooth`;
  - then `sender: ESP32 AA:BB:... received severity=critical (test injected by pi_bt_server.py)`.
- **ESP32 Serial Monitor:**
  ```
  [bt] CONNECTED to the Pi (D8:3A:DD:..:..:.. channel 4)
  [pi] Drowsy  severity=critical  "test injected by pi_bt_server.py"  p(Drowsy)=0.95
  !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
  !!! CRITICAL alert received from the Pi: test injected by pi_bt_server.py
  !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
  [fusion] would raise a ... alert (grip ...) -- not sent, link test mode
  ```
  Every 10 s it also prints `[bt] Pi link UP (...): N session(s), M record(s) since boot, last
  answer 0.4 s ago`.

**4. End to end with the camera.** Stop the step-2 server (Ctrl+C); `demo.sh` starts its own:
```sh
scripts/demo.sh --camera picam --bluetooth
```
Close your eyes for 3 s. The stream shows CRITICAL, the Pi logs `received severity=critical (eyes
closed 3.0 s)`, and the ESP32 prints the banner. That's about 1–3 s after the screen, because the
Pi re-sends the current reading every 2 s and the ESP32 polls every second in this mode. The
metrics log is now `data/metrics.jsonl` on the Pi.

**If it doesn't connect:**

| What you see | Likely cause |
|---|---|
| ESP32 prints `Can't reach the Pi`, Pi logs nothing | Wrong `PI_BT_MAC_ADDRESS`, Pi server not running, Pi adapter off (`bluetoothctl power on`), or a channel mismatch |
| Same, after pairing worked once | The Pi forgot the pairing, or the ESP32 was reflashed with a new bond: `remove <ESP32 MAC>` in `bluetoothctl` and pair again |
| `Can't listen on RFCOMM channel 4` | Something else already holds channel 4, e.g. another `pi_bt_server.py` still running |
| `Can't open data/buffer.sqlite3` | Rootful Docker created `data/` as root: `sudo chown -R $USER data` |
| Hundreds of old readings scroll past first | The queue kept readings from an earlier run. Stop everything, then `rm -f data/buffer.sqlite3*` |

`BT_LINK_TEST_MODE 1` sends nothing to the backend, so set it back to `0` for a real truck.

## Demo: watching it work

### On a laptop

```sh
OUTPUTS=logging,mjpeg docker compose up --build   # first run, or after a rebuild-triggering change
OUTPUTS=logging,mjpeg docker compose up           # every run after that — reuses the built image
```

Then open **`http://localhost:8080/stream`** in a browser. If your webcam isn't at
`/dev/video0`, edit `docker-compose.yml`'s `devices:` entry to match, or drop it and set
`CAMERA_SOURCE` to a recorded video file path instead — a good, zero-hardware-risk fallback if
you'd rather not depend on a live camera and decent lighting for a demo.

### On the Raspberry Pi 5

Uses the CSI camera via a separate overlay file that adds the right device passthrough,
`SOURCE=picamera`, and `restart: unless-stopped` on top of the base compose file. Once per Pi,
run the host setup script first (rootless Docker can't use the host's `video` group, so the
camera's dma-heap nodes need a per-user ACL — see the script's header):

```sh
sudo scripts/pi-host-setup.sh
OUTPUTS=logging,mjpeg docker compose -f docker-compose.yml -f docker-compose.pi.yml up --build
```

On arm64 the Dockerfile installs `python3-picamera2`/`python3-libcamera` from Raspberry Pi's own
apt repo (bookworm suite) — they're not on PyPI or in Debian's own repo, so don't add them to
`requirements.txt` or a plain `apt-get install` line.

(drop `--build` on later runs, same as above, once `argus/cv-argus:dev` already exists on the
Pi)

Then open **`http://<pi-ip>:8080/stream`** from any device's browser on the same network (find
the Pi's IP with `hostname -I` on the Pi itself).

The device list in `docker-compose.pi.yml` was taken from a real Pi 5 with an IMX219 camera, and
picamera2 inside this image has captured frames through it. A different board/camera/kernel may
number nodes differently — if `up` fails on a missing device or no camera is listed, compare
`ls /dev/media* /dev/video* /dev/v4l-subdev* /dev/dma_heap/` on the Pi against that file. CPU
performance for this model on a Pi 5 is also unmeasured — if it feels slow, that's expected to
be checked, not a sign something's broken.

## Finding the bottleneck

Every stage logs a timing summary every `LATENCY_LOG_INTERVAL` seconds (default 10). Watch
them with `docker compose logs -f` (or straight to the console with `docker compose up`):

```
stats video_capture.produce: n=50 proc(mean=3.2ms p50=3 p95=6 max=12) drop=0 life(n=250 max=33ms)
stats face_detector_crop.process: n=50 wait(mean=0.3ms ...) proc(mean=2.4ms p50=2.2 p95=3.4 max=4.1) inq(avg=0.0 max=1) drop=0 life(...)
stats parts_inference.process: n=50 wait(mean=0.2ms ...) proc(mean=1.9ms ...) crop(mean=0.7ms ...) infer(mean=0.7ms ...) decode(mean=0.3ms ...) rules(mean=0.0ms ...) inq(avg=0.0 max=1) drop=0 life(...)
stats logging_output.process: n=50 wait(mean=0.1ms ...) proc(mean=0.1ms ...) e2e(mean=15ms p95=17) drop=0 life(...)
```

A frame's whole trip, and which number is which:

```
video_capture.produce  proc = grabbing one frame (cap.read)
   │  queue
face_detector_crop      wait = time queued   proc = MediaPipe BlazeFace
   │  queue
parts_inference         wait = time queued   proc = crop + model + decode + tracker + rules
   │  queue
logging_output          wait = time queued   proc ≈ 0       e2e = grab-to-here (the whole trip)
```

- **`proc(mean=...)`** — work done *inside* the stage. The **largest one is the compute
  bottleneck** (here `face_detector_crop`, BlazeFace, at ~2.4 ms).
- **`wait(mean=...)`** — how long the frame sat in that stage's input queue first. The stage
  with the **biggest `wait`** is where frames pile up (it's right in front of the bottleneck).
  All the `wait`s + all the `proc`s ≈ the sink's `e2e`; the rest is scheduling.
- **`crop` / `infer` / `decode` / `rules`** on `parts_inference` — that stage's `proc` split
  into its steps. `infer` is the model itself (~0.7 ms int8, ~5.5 ms float); float in the
  hundreds of ms would mean its `tf.function` is re-tracing every frame — a regression.
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

`LATENCY_LOG_INTERVAL=0` turns all of this off. For per-frame numbers rather than interval
summaries, use the metrics log ("Measuring it for the report", above).

## Troubleshooting

**The int8 model fails on the Raspberry Pi.** Nothing to do by hand: at startup the container
loads the int8 model in a throwaway child process, and if that crashes or misbehaves it uses the
float `.keras` instead. Look for this log line:

```
WARNING parts detector: int8 probe failed: <why> -- falling back to the float .keras model
```

The footer, the log lines and `metrics.jsonl` then say `float`. The Docker build already runs
the same check (`RUN python -m cv_argus.model.parts_probe`) and prints `int8 OK/FAILED`, so a
build on the Pi tells you up front. Float is ~5.5 ms per face on a desktop CPU instead of
~0.7 ms (still small next to BlazeFace). Set `PARTS_INT8_FALLBACK=0` to make an int8 failure
stop the container instead.

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
pip install pytest        # not in requirements.txt on purpose -- see CLAUDE.md's "Tests"
pytest                    # the full unit suite (223 tests)
```

The default run is **hermetic** — no network, no camera, no Google Drive, no model download.
Every downloader test either exercises the skip-if-cached path or monkeypatches `gdown` /
`urllib`. It needs `tensorflow` + `mediapipe` + `opencv` installed (the same deps the app
needs); `pip install -e .` pulls them in.

**This same suite now also runs automatically inside `docker build`/`docker compose build`**,
as a real build gate, not a separate manual step — placed right before the expensive ~1GB
model-artifact download from Drive, so a broken test fails the build fast rather than paying for
that download first. See `CLAUDE.md`'s "Tests" section for the full mechanism and how it's
verified both to pass and to actually block a broken build. Nothing extra to run yourself here —
it's just what `docker compose up --build` already does.
`tests/` mirrors `src/`: `test_model_*`, `test_pipeline_*` (including `test_pipeline_latency.py`
for the `StageStats` instrumentation), `test_alerts_*`, `test_buffer_*`, `test_orchestrator_*`,
`test_sender_*`, and `test_main.py`. The parts detector is tested with **stub** backends and
ideal output grids — the real model files are never loaded in the unit suite. The alert-pipeline
tests are just as hermetic in kind: `test_buffer_store.py`/`test_sender_server.py` use a real
SQLite file under `tmp_path` (never a fixed path), `test_sender_*` uses an in-memory
`FakeTransport`/`FakeListener` pair instead of a real Bluetooth socket, and
`test_orchestrator_decision.py` uses a plain duck-typed stand-in for `DetectionResult` instead
of the real (tensorflow-backed) class. All of that is part of the default `pytest` run above,
not a separate tier. There is a second, opt-in tier that actually builds and runs the Docker image:

```sh
CV_ARGUS_DOCKER_TESTS=1 pytest -m docker
```

It is deselected from a plain `pytest` and additionally skips itself unless `docker` is on
`PATH` and `CV_ARGUS_DOCKER_TESTS=1` — the build pulls ~1GB of TensorFlow plus the model
artifacts and takes minutes. It checks the image builds, `import cv_argus` works inside it,
the three model artifacts are baked into `/app/models`, re-running the downloaders is a cached
no-op, and the `SOURCE`/`OUTPUTS` guards reject bad values.

## Where to go next

- [`CLAUDE.md`](CLAUDE.md) — the real architecture: the `Stage`/`Pipeline` threading design, the
  parts pipeline's training-fidelity rules, thresholds and measurements, the `orchestrator/`/`buffer/`/`sender/`/
  `alerts/` design (decision loop, SQLite schema, the Bluetooth PULL/ACK protocol), exact model
  input/output shapes, and every convention worth knowing before changing code here.
- [`tests/`](tests/) — the `pytest` suite (mirrors `src/`: `test_model_*`, `test_pipeline_*`,
  `test_alerts_*`, `test_buffer_*`, `test_orchestrator_*`, `test_sender_*`, `test_main.py`, plus
  the opt-in `test_docker_build.py`).
- [`scripts/smoke_test_pipeline.py`](scripts/smoke_test_pipeline.py) — a synthetic test of the
  threading/queue plumbing itself, runnable with no camera, no model, and none of `cv2`/
  `mediapipe`/`tensorflow` installed. `tests/test_pipeline_stage.py` is the maintained `pytest`
  version of the same checks.
- **What's still genuinely open, not just undocumented**: real Bluetooth device passthrough
  isn't wired into either compose file yet (`SENDER_TRANSPORT=none` is this file's safe
  default — see "Prerequisites" and `CLAUDE.md`'s "Open decisions"); no ESP32 firmware/hardware
  exists in this repo, so `BluetoothSppTransport` itself is untested against a real device; and
  the detector hasn't been measured on a Pi 5, real cabin NoIR footage, or unseen drivers (see
  `CLAUDE.md`'s "Current status").
