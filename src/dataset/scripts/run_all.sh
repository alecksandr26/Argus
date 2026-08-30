#!/usr/bin/env bash
# End-to-end local dataset build: fetch models -> 01/02/06 -> 09 -> verify.
#
# raw/raw_videos/ must already hold binary-labelled clips
# (subject_NN/level_<1-2>_clip_NN.mp4; level_1 = Not Drowsy, level_2 = Drowsy).
#
# Usage:  scripts/run_all.sh [--reset] [--workers N] [--only 01,02,06,09]
#
# A single Ctrl-C is forwarded to the running child, which pauses and checkpoints; re-run this
# script (same args) to resume. Steps are individually resumable, so re-running is always safe.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATASET_DIR="$(dirname "$HERE")"

RESET=""; WORKERS=""; ONLY="01,02,06,09"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --reset)   RESET="--reset"; shift ;;
    --workers) WORKERS="--workers $2"; shift 2 ;;
    --only)    ONLY="$2"; shift 2 ;;
    *)         echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

run() { echo; echo "=== $* ==="; "$@"; }
has() { [[ ",$ONLY," == *",$1,"* ]]; }

cd "$DATASET_DIR"

run python scripts/fetch_models.py

has 01 && run python scripts/build_lstm_windows.py $WORKERS $RESET
has 02 && run python scripts/build_frame_features.py --enrich $WORKERS $RESET
has 06 && run python scripts/build_face_crops.py $WORKERS $RESET
has 09 && run python scripts/build_cnn_lstm_windows.py $WORKERS $RESET

run python scripts/verify_artifacts.py

echo
echo "All done. Next: python scripts/publish_to_drive.py"
