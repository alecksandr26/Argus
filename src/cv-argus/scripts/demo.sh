#!/usr/bin/env bash
# Live demo of cv-argus in Docker with the annotated MJPEG stream: tiny YOLO + tracker + rules,
# int8 on webcam 0 by default. Every frame is also logged and written to the metrics log.
#
#   scripts/demo.sh                          # int8, webcam 0
#   scripts/demo.sh --precision float        # same detector, float .keras (compare accuracy/speed)
#   scripts/demo.sh --camera 1               # another webcam index (or /dev/video2)
#   scripts/demo.sh --camera src/demo_clips/clip.mp4   # a recorded video file (must be under src/)
#   scripts/demo.sh --camera picam           # the Pi 5 CSI camera (adds docker-compose.pi.yml)
#   scripts/demo.sh --eye-threshold 0.35 --head-threshold 0.25   # more sensitive closed/down
#   scripts/demo.sh --port 8081 --width 960  # stream port / downscaled stream width
#
# Then open http://localhost:<port>/stream (or http://<pi-ip>:<port>/stream on the Pi).
# Thresholds: 0.5 = argmax, lower = more sensitive (defaults 0.40 closed eye, 0.30 head down).
# Measurements for the report: the metrics log is /app/data/metrics.jsonl in the container:
#   docker compose cp cv-argus:/app/data/metrics.jsonl . && python3 scripts/summarize_metrics.py metrics.jsonl
# RULE_* timers (src/constants.py) are overridden in src/cv-argus/.env (compose loads it), e.g.
#   echo RULE_EYES_CRITICAL_S=2.5 >> .env
set -euo pipefail
cd "$(dirname "$0")/.."

precision=int8 camera=0 port=8080 width="" eye_thr="" head_thr=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --precision)      precision="$2"; shift 2 ;;
    --camera)         camera="$2"; shift 2 ;;
    --port)           port="$2"; shift 2 ;;
    --width)          width="$2"; shift 2 ;;
    --eye-threshold)  eye_thr="$2"; shift 2 ;;
    --head-threshold) head_thr="$2"; shift 2 ;;
    -h|--help)        sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

compose=(docker compose -f docker-compose.yml)
export PARTS_PRECISION="$precision" OUTPUTS=logging,metrics,mjpeg \
       DEMO_STREAM_PORT="$port" DEMO_STREAM_WIDTH="$width" \
       PARTS_CLOSED_EYE_THRESHOLD="$eye_thr" PARTS_HEAD_DOWN_THRESHOLD="$head_thr"
if [[ "$camera" == "picam" ]]; then
  compose+=(-f docker-compose.pi.yml)
  export SOURCE=picamera
elif [[ -f "$camera" ]]; then
  # A video file. Only src/ is bind-mounted into the container (at /app/src), so the clip must
  # live under src/cv-argus/src/, e.g. src/demo_clips/eyes.mp4 (gitignored: *.mp4).
  rel="${camera#./}"
  if [[ "$rel" != src/* ]]; then
    echo "put the clip under src/cv-argus/src/ (e.g. src/demo_clips/) so the container can see it" >&2
    exit 2
  fi
  export SOURCE=video_capture CAMERA_SOURCE="/app/$rel"
else
  export SOURCE=video_capture CAMERA_SOURCE="$camera"
fi

echo "cv-argus demo: PARTS_PRECISION=$PARTS_PRECISION SOURCE=$SOURCE CAMERA_SOURCE=${CAMERA_SOURCE:-picam}" \
     "closed_eye>=${eye_thr:-default} head_down>=${head_thr:-default}"
echo "stream: http://localhost:${port}/stream   metrics: /app/data/metrics.jsonl (in the container)"
exec "${compose[@]}" up --build
