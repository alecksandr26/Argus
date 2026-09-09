"""Entry point for the cv-argus edge process.

Builds one `Pipeline` (see `cv_argus.pipeline.stage`) end to end — a `Source` stage reads
frames, two MediaPipe stages produce a face crop and a geometric-feature vector from it, an
inference stage turns both into a `DetectionResult`, and one or more output stages do something
with it — starts it, and runs until stopped.

There is one pipeline shape: `FaceDetectorCropStage` -> `FaceLandmarkerCropStage` ->
`FusedInferenceStage` (the frozen-CNN-embedding + geometric-feature + LSTM classifier — see
`src/cv-argus/CLAUDE.md`'s "Current status" for its measured accuracy and caveats). Earlier
versions of this module supported a `PIPELINE` env var switching between this, a single-frame
CNN, and a windowed-geometric-only LSTM — both of those were removed once this fused pipeline's
result made them obsolete; see the root `CLAUDE.md` for why.

Env vars:

- `SOURCE` (default `"video_capture"`) — where frames come from: `"video_capture"`
  (`cv2.VideoCapture`, driven by `CAMERA_SOURCE` — a camera index, a `/dev/videoN` path, or a
  video file) or `"picamera"` (the Pi 5's CSI camera via `picamera2`).
- `OUTPUTS` (default `"logging"`) — comma-separated list of sinks to attach, fanned out from the
  same inference stage via `Stage.connect()` (see `pipeline/stage.py`) — no new plumbing needed
  to run more than one at once. `"logging"` (`LoggingOutputStage`, text) and/or `"mjpeg"`
  (`MjpegStreamOutputStage`, a browser-viewable annotated video stream — demo-only, see that
  module's docstring for why it isn't on by default: no authentication, and this project's own
  stated cargo-theft/security risk model makes an open camera stream a real exposure to leave
  running).
- `DEMO_STREAM_HOST`/`DEMO_STREAM_PORT` (default `"0.0.0.0"`/`8080`) — only read if `OUTPUTS`
  includes `"mjpeg"`.
- `LOG_LEVEL` (default `"INFO"`) — root log level. `DEBUG` also surfaces `LoggingOutputStage`'s
  per-frame lines and the drop-oldest debug logs.
- `LATENCY_LOG_INTERVAL` (default `"10"`) — seconds between per-stage `StageStats` report
  lines (timing, input-queue depth, dropped-frame count, end-to-end latency at the sink — see
  `cv_argus.pipeline.latency`). `0` disables them.
"""

import logging
import os
import signal
import threading

from cv_argus.model import FusedDrowsinessDetector
from cv_argus.pipeline import (
    FaceDetectorCropStage,
    FaceLandmarkerCropStage,
    FusedInferenceStage,
    LoggingOutputStage,
    MjpegStreamOutputStage,
    OutputStage,
    PiCameraSource,
    Pipeline,
    SourceStage,
    VideoCaptureSource,
    download_face_detector_bundle,
    download_face_landmarker_bundle,
)

logger = logging.getLogger(__name__)


def _camera_source() -> int | str:
    """Parse `CAMERA_SOURCE` the way the rest of this project already documents it: an integer
    camera index, a `/dev/videoN` device path, or a video file path — all three are valid
    `cv2.VideoCapture` sources, so `VideoCaptureSource` doesn't need to know which one this is
    (see `cv_argus.pipeline.sources`)."""
    raw = os.environ.get("CAMERA_SOURCE", "0")
    try:
        return int(raw)
    except ValueError:
        return raw


def _build_source() -> SourceStage:
    kind = os.environ.get("SOURCE", "video_capture").strip().lower()
    if kind == "video_capture":
        return VideoCaptureSource(_camera_source())
    if kind == "picamera":
        return PiCameraSource()
    raise SystemExit(f"Unknown SOURCE={kind!r} -- expected 'video_capture' or 'picamera'")


_OUTPUT_BUILDERS = {
    "logging": lambda: LoggingOutputStage(),
    "mjpeg": lambda: MjpegStreamOutputStage(
        host=os.environ.get("DEMO_STREAM_HOST", "0.0.0.0"),
        port=int(os.environ.get("DEMO_STREAM_PORT", "8080")),
    ),
}


def _build_outputs() -> list[OutputStage]:
    names = [n.strip() for n in os.environ.get("OUTPUTS", "logging").split(",") if n.strip()]
    if not names:
        raise SystemExit("OUTPUTS is set but empty -- expected at least one of " f"{sorted(_OUTPUT_BUILDERS)}")
    outputs = []
    for name in names:
        builder = _OUTPUT_BUILDERS.get(name)
        if builder is None:
            raise SystemExit(f"Unknown output {name!r} in OUTPUTS -- expected one of {sorted(_OUTPUT_BUILDERS)}")
        outputs.append(builder())
    return outputs


def _latency_log_interval() -> float:
    """Seconds between `StageStats` report lines (`LATENCY_LOG_INTERVAL`, default 10; 0 = off).
    A malformed value falls back to the default rather than aborting startup over a logging
    knob."""
    raw = os.environ.get("LATENCY_LOG_INTERVAL", "10").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        logger.warning("LATENCY_LOG_INTERVAL=%r is not a number, using 10", raw)
        return 10.0


def _build_pipeline() -> Pipeline:
    face_detector_bundle = download_face_detector_bundle()
    face_landmarker_bundle = download_face_landmarker_bundle()
    detector = FusedDrowsinessDetector.from_env()

    source = _build_source()
    crop_stage = FaceDetectorCropStage(face_detector_bundle)
    landmarker_crop_stage = FaceLandmarkerCropStage(face_landmarker_bundle)
    inference_stage = FusedInferenceStage(detector)
    outputs = _build_outputs()

    source.connect(crop_stage).connect(landmarker_crop_stage).connect(inference_stage)
    for output in outputs:
        inference_stage.connect(output)
    return Pipeline(
        [source, crop_stage, landmarker_crop_stage, inference_stage, *outputs],
        stats_interval=_latency_log_interval(),
    )


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    logger.info(
        "cv-argus starting (SOURCE=%s, OUTPUTS=%s, LATENCY_LOG_INTERVAL=%s)",
        os.environ.get("SOURCE", "video_capture"),
        os.environ.get("OUTPUTS", "logging"),
        os.environ.get("LATENCY_LOG_INTERVAL", "10"),
    )
    pipeline = _build_pipeline()

    stop_event = threading.Event()

    def _handle_signal(signum, _frame) -> None:
        logger.info("Received signal %s, shutting down...", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    pipeline.start()
    try:
        # Wake up periodically rather than blocking on stop_event forever: a video-file source
        # reaching EOF stops the pipeline's own threads without ever setting stop_event, and
        # this is what notices that and lets the process exit instead of hanging.
        while not stop_event.is_set() and pipeline.is_alive():
            stop_event.wait(timeout=1.0)
    finally:
        logger.info("Stopping pipeline...")
        pipeline.stop()
        logger.info("cv-argus stopped")


if __name__ == "__main__":
    main()
