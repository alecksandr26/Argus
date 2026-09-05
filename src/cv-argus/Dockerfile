# syntax=docker/dockerfile:1

# Debian slim (glibc), not Alpine: MediaPipe and TensorFlow only ship manylinux wheels
# built against glibc, and won't install (or won't run) on musl-based images like Alpine.
# Raspberry Pi OS (64-bit, Bookworm) is Debian Bookworm under the hood, so this same
# Dockerfile builds and runs unmodified on both a dev machine (linux/amd64) and the
# Raspberry Pi 5 (linux/arm64) — e.g. `docker buildx build --platform linux/amd64,linux/arm64`.
FROM python:3.11-slim-bookworm

# Runtime libraries opencv-python-headless / mediapipe dlopen even when running headless:
#   libgl1          - OpenCV's video/image codec paths link against GL
#   libglib2.0-0    - glib, used by OpenCV's plugin loading
#   libsm6/libxext6/libxrender1 - legacy X11 libs some OpenCV builds still probe for
#   libgles2/libegl1 - MediaPipe Tasks' native C bindings (mediapipe_c_bindings) dlopen
#                      libGLESv2.so.2/libEGL.so.1 for their GPU tensor-op path even when the
#                      pipeline never touches a display — missing these fails with
#                      "OSError: libGLESv2.so.2: cannot open shared object file" the first time
#                      any FaceDetector/FaceLandmarker is actually constructed (not at import
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

# Install pinned deps before copying source so `docker build` cache reuses this (slow,
# mediapipe/tensorflow-heavy) layer as long as requirements.txt hasn't changed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# setup.py maps the `src/` directory onto the `cv_argus` package name (see setup.py for
# why). `-e` (editable) registers it as a path reference back to /app/src rather than
# copying it into site-packages, so the docker-compose dev bind-mount of ./src:/app/src
# keeps working without rerunning pip install after every source edit. --no-deps because
# requirements.txt already installed everything in install_requires. pyproject.toml has to be
# copied in too (see that file) -- without it pip falls back to a legacy editable-install mode
# that ignores setup.py's package_dir remap entirely and `import cv_argus` fails at runtime.
COPY pyproject.toml setup.py .
COPY src ./src
RUN pip install --no-cache-dir --no-deps -e .

COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# Model artifacts (the fused CNN-embedding/LSTM classifier this container deploys, the CNN
# checkpoint it depends on as a frozen embedding backbone, and the two MediaPipe bundles both
# pipeline stages need) are baked into the image at BUILD time, not fetched at container start —
# see model/downloader.py and pipeline/downloader.py. This device has to be able to boot and
# start monitoring the driver in a moving truck that may have no signal right then; a runtime
# download dependency is a liability a build-time one isn't. Accepted trade-off: a new trained
# model needs an image rebuild + redeploy, not just a container restart. `docker build` succeeds
# with no ARG/`.env` at all — both Drive file ids have checked-in defaults in constants.py — and
# both downloads are required: a failure in either fails the whole build (see
# model/downloader.py's `__main__` block).
ENV MODEL_DIR=/app/models
ARG CNN_MODEL_DRIVE_FILE_ID
ENV CNN_MODEL_DRIVE_FILE_ID=${CNN_MODEL_DRIVE_FILE_ID}
ARG FUSED_MODEL_DRIVE_FILE_ID
ENV FUSED_MODEL_DRIVE_FILE_ID=${FUSED_MODEL_DRIVE_FILE_ID}
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
