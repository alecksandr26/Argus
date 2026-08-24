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
builds and runs the CNN pipeline (the model this project currently focuses on and deploys by
default) against whatever's at `/dev/video0`, and logs each frame's drowsiness classification to
the console. The trained model's Google Drive file ID has a checked-in default, so the build
doesn't need one supplied.

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
│  STATUS: Alert  (green)  │
│  fps: 11.2               │
└──────────────────────────┘
```

`STATUS` is color-coded — green for `Alert`, amber for `Low Vigilant`, red for `Drowsy` — and
comes straight from the model, so it'll be as reliable (or not) as the model currently is; see
`CLAUDE.md`'s "Current status" for the honest caveat on that.

## Configuration

Everything below is an environment variable — set inline (`VAR=value docker compose up
--build`), or in a `.env` file (`cp .env.example .env` first; see that file for the full list
including model-artifact overrides not covered here).

| Variable | Default | What it does |
|---|---|---|
| `CAMERA_SOURCE` | `0` | Passed to `cv2.VideoCapture`: an integer camera index, a `/dev/videoN` path, or a video file path (for testing/demoing with no camera attached). |
| `PIPELINE` | `cnn` | Which model runs: `cnn` (the current default/deployed model) or `lstm` (kept, optional — needs `MODEL_DRIVE_FILE_ID` set to actually download a model). |
| `SOURCE` | `video_capture` | Where frames come from: `video_capture` (`cv2.VideoCapture`, driven by `CAMERA_SOURCE`) or `picamera` (the Pi 5's CSI camera — see "On the Pi 5" below). |
| `OUTPUTS` | `logging` | Comma-separated sink(s): `logging` (text only) and/or `mjpeg` (the browser-viewable demo stream — e.g. `OUTPUTS=logging,mjpeg`). |
| `DEMO_STREAM_PORT` | `8080` | Only read when `OUTPUTS` includes `mjpeg`. Change if `8080` is already taken on your machine. |

**`mjpeg` has no authentication.** It's meant for demos on a network you trust, not for leaving
on — see `CLAUDE.md`'s "Demo" section for why this matters more than usual for this project.

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
  they do (`docker compose logs` will show `cv-argus starting (PIPELINE=..., SOURCE=...,
  OUTPUTS=...)` on startup, confirming what it's actually using).
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

## Where to go next

- [`CLAUDE.md`](CLAUDE.md) — the real architecture: the `Stage`/`Pipeline` threading design, why
  the CNN is the current focus vs. the LSTM's long-term intended role, exact model
  input/output shapes, and every convention worth knowing before changing code here.
- [`scripts/smoke_test_pipeline.py`](scripts/smoke_test_pipeline.py) — a synthetic test of the
  threading/queue plumbing itself, runnable with no camera, no model, and none of `cv2`/
  `mediapipe`/`tensorflow` installed.
