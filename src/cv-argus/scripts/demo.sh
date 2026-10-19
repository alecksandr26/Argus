#!/usr/bin/env bash
# Live demo of cv-argus in Docker with the annotated MJPEG stream. The defaults run the new
# "parts" pipeline (tiny YOLO + tracker + rules) at int8 on webcam 0.
#
#   scripts/demo.sh                          # parts, int8, webcam 0
#   scripts/demo.sh --precision float        # same detector, float .keras (compare accuracy/speed)
#   scripts/demo.sh --camera 1               # another webcam index (or /dev/video2)
#   scripts/demo.sh --camera src/demo_clips/clip.mp4   # a recorded video file (must be under src/)
#   scripts/demo.sh --camera picam           # the Pi 5 CSI camera (adds docker-compose.pi.yml)
#   scripts/demo.sh --pipeline fused         # the old CNN+LSTM pipeline, for comparison
#   scripts/demo.sh --port 8081 --width 960  # stream port / downscaled stream width
#
# Then open http://localhost:<port>/stream (or http://<pi-ip>:<port>/stream on the Pi).
# RULE_* thresholds (src/constants.py) are overridden in src/cv-argus/.env (compose loads it), e.g.
#   echo RULE_EYES_CRITICAL_S=2.5 >> .env
set -euo pipefail
cd "$(dirname "$0")/.."

pipeline=parts precision=int8 camera=0 port=8080 width=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --pipeline)  pipeline="$2"; shift 2 ;;
    --precision) precision="$2"; shift 2 ;;
    --camera)    camera="$2"; shift 2 ;;
    --port)      port="$2"; shift 2 ;;
    --width)     width="$2"; shift 2 ;;
    -h|--help)   sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

compose=(docker compose -f docker-compose.yml)
export PIPELINE="$pipeline" PARTS_PRECISION="$precision" OUTPUTS=logging,mjpeg \
       DEMO_STREAM_PORT="$port" DEMO_STREAM_WIDTH="$width"
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

echo "cv-argus demo: PIPELINE=$PIPELINE PARTS_PRECISION=$PARTS_PRECISION SOURCE=$SOURCE CAMERA_SOURCE=${CAMERA_SOURCE:-picam}"
echo "stream: http://localhost:${port}/stream"
exec "${compose[@]}" up --build
