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
#   ffmpeg          - video container/codec support for cv2.VideoCapture (camera + files)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
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
# requirements.txt already installed everything in install_requires.
COPY setup.py .
COPY src ./src
RUN pip install --no-cache-dir --no-deps -e .

COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# Downloaded/cached model artifacts live here — mount a volume so a new model version
# doesn't require rebuilding the image, and so the download is skipped on restart.
ENV MODEL_DIR=/app/models
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
