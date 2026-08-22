#!/usr/bin/env sh
# Model artifacts (trained LSTM model, MediaPipe Face Landmarker bundle) are baked into the
# image at BUILD time (see the Dockerfile, model/downloader.py, pipeline/downloader.py) rather
# than fetched here at container start — so this script just hands off to whatever CMD was
# requested (currently `python -m cv_argus.main`).
set -e

exec "$@"
