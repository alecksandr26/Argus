# syntax=docker/dockerfile:1

# Debian slim (glibc), not Alpine: MediaPipe and TensorFlow only ship manylinux wheels
# built against glibc, and won't install (or won't run) on musl-based images like Alpine.
# Raspberry Pi OS (64-bit, Bookworm) is Debian Bookworm under the hood, so this same
# Dockerfile builds and runs unmodified on both a dev machine (linux/amd64) and the
# Raspberry Pi 5 (linux/arm64) — e.g. `docker buildx build --platform linux/amd64,linux/arm64`.
FROM python:3.11-slim-bookworm AS base

# Runtime libraries opencv-python-headless / mediapipe dlopen even when running headless:
#   libgl1          - OpenCV's video/image codec paths link against GL
#   libglib2.0-0    - glib, used by OpenCV's plugin loading
#   libsm6/libxext6/libxrender1 - legacy X11 libs some OpenCV builds still probe for
#   libgles2/libegl1 - MediaPipe Tasks' native C bindings (mediapipe_c_bindings) dlopen
#                      libGLESv2.so.2/libEGL.so.1 for their GPU tensor-op path even when the
#                      pipeline never touches a display — missing these fails with
#                      "OSError: libGLESv2.so.2: cannot open shared object file" the first time
#                      any FaceDetector is actually constructed (not at import
#                      time, so `pip install` and the download step both succeed regardless).
#   ffmpeg          - video container/codec support for cv2.VideoCapture (camera + files)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        libgles2 \
        libegl1 \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Unbuffered stdout/stderr: without this Python block-buffers its output when the container
# isn't attached to a TTY, so `docker compose logs` (and the per-stage StageStats latency
# lines in particular -- see cv_argus.pipeline.latency) arrive in delayed bursts instead of
# streaming.
ENV PYTHONUNBUFFERED=1

# Install pinned deps before copying source so `docker build` cache reuses this (slow,
# mediapipe/tensorflow-heavy) layer as long as requirements.txt hasn't changed.
# arm64 (the Pi) only: the CSI camera stack (`SOURCE=picamera`). picamera2 imports `libcamera`,
# whose Python bindings are NOT on PyPI — they only ship as Raspberry Pi OS apt packages, built
# against the matching libcamera (with the Pi 5 PiSP pipeline handler + sensor tuning files in
# libcamera-ipa). So both come from Raspberry Pi's own apt repo rather than pip: its *bookworm*
# suite, matching this image's Debian 12 / Python 3.11 ABI — not whatever the host Pi OS release
# is (the host's own python3-libcamera is built for the host's Python and can't be mounted in).
# apt installs them into /usr/lib/python3/dist-packages, which this image's /usr/local Python
# doesn't search by default; the .pth file *appends* it to sys.path, after site-packages, so the
# pip-pinned numpy/TF/OpenCV below still win over any older Debian copies apt pulls in as deps.
# Skipped on amd64, where SOURCE=picamera isn't usable anyway.
ARG TARGETARCH
RUN if [ "$TARGETARCH" = "arm64" ] || [ "$(uname -m)" = "aarch64" ]; then \
        mkdir -p /etc/apt/keyrings \
        && python -c "import urllib.request; urllib.request.urlretrieve('https://archive.raspberrypi.com/debian/raspberrypi.gpg.key', '/etc/apt/keyrings/raspberrypi.asc')" \
        && echo "deb [signed-by=/etc/apt/keyrings/raspberrypi.asc] http://archive.raspberrypi.com/debian bookworm main" \
            > /etc/apt/sources.list.d/raspi.list \
        && apt-get update \
        && apt-get install -y --no-install-recommends python3-picamera2 libcamera-ipa \
        && rm -rf /var/lib/apt/lists/* \
        && echo /usr/lib/python3/dist-packages \
            > "$(python -c 'import site; print(site.getsitepackages()[0])')/zz-debian-dist-packages.pth"; \
    fi

RUN pip install --no-cache-dir --upgrade pip
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# setup.py maps the `src/` directory onto the `cv_argus` package name (see setup.py for
# why). `-e` (editable) registers it as a path reference back to /app/src rather than
# copying it into site-packages, so the docker-compose dev bind-mount of ./src:/app/src
# keeps working without rerunning pip install after every source edit. --no-deps because
# requirements.txt already installed everything in install_requires. pyproject.toml has to be
# copied in too (see that file) -- without it pip falls back to a legacy editable-install mode
# that ignores setup.py's package_dir remap entirely and `import cv_argus` fails at runtime.
COPY pyproject.toml setup.py ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps -e .

COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# --- test gate -----------------------------------------------------------------------------
# Runs the hermetic pytest tier (no network/camera/Drive — see pyproject.toml's
# `addopts = -m 'not docker'` and this module's own CLAUDE.md's "Tests" section) right here,
# right after the package becomes importable and *before* the expensive part of the build (the
# ~1GB model-artifact download from Drive below): a broken test fails fast, without paying for
# that download first. `runtime` below is FROM `base`, not FROM this stage, and pulls back only
# `/app/src` via `COPY --from=test` — a no-op content-wise (identical to what `base` already
# has; this stage only additionally copied `tests/` alongside it) that exists purely to force
# Docker to build and pass this stage before `runtime` can be built at all. `tests/` itself
# never reaches the final image.
FROM base AS test
# pytest itself isn't in requirements.txt on purpose — that file is the exact, pinned dependency
# list this image ships to the Pi, and pytest has no business being part of it. Installed here,
# scoped to this discarded stage only, rather than added to requirements.txt.
RUN pip install --no-cache-dir pytest
COPY tests ./tests
RUN pytest

FROM base AS runtime
COPY --from=test /app/src ./src

# Model artifacts (notebook 12's tiny YOLO parts detector at both precisions, int8 .tflite +
# float .keras, and the MediaPipe BlazeFace bundle) are baked into the image at BUILD time, not
# fetched at container start — see model/downloader.py and pipeline/downloader.py. This device
# has to be able to boot and start monitoring the driver in a moving truck that may have no
# signal right then; a runtime download dependency is a liability a build-time one isn't.
# Accepted trade-off: a new trained model needs an image rebuild + redeploy, not just a
# container restart. `docker build` succeeds with no ARG/`.env` at all — both Drive file ids
# have checked-in defaults in constants.py — and every download is required: a failure fails
# the whole build. (The old CNN+LSTM artifacts are no longer downloaded; that pipeline lives on
# branch archive/cnn-lstm-fused.)
ENV MODEL_DIR=/app/models
ARG PARTS_INT8_DRIVE_FILE_ID
ENV PARTS_INT8_DRIVE_FILE_ID=${PARTS_INT8_DRIVE_FILE_ID}
ARG PARTS_FLOAT_DRIVE_FILE_ID
ENV PARTS_FLOAT_DRIVE_FILE_ID=${PARTS_FLOAT_DRIVE_FILE_ID}
RUN python -m cv_argus.model.downloader && python -m cv_argus.pipeline.downloader

# Still a volume even though the image already has the files: an empty named volume is
# seeded from the image's /app/models contents on first mount (standard Docker behavior), so
# this doesn't change first-run behavior — it's kept as an escape hatch for dropping a newer
# model into a running container without a full rebuild, without being the primary path.
VOLUME ["/app/models"]

# buffer/'s SQLite file lives here — it's not a service, just a file this process opens
# directly (Python's stdlib sqlite3), but it still needs its own volume so queued alerts
# survive a container restart/rebuild. Kept separate from MODEL_DIR: the model cache and
# the live alert queue have different lifecycles (e.g. you may want to wipe/back up one
# without touching the other).
ENV BUFFER_DIR=/app/data
VOLUME ["/app/data"]

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["python", "-m", "cv_argus"]
