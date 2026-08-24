"""Entry point for the cv-argus edge process.

Builds one `Pipeline` (see `cv_argus.pipeline.stage`) end to end — a `Source` stage reads
frames, a MediaPipe stage extracts whatever its downstream model needs, an inference stage
turns that into a `DetectionResult`, and one or more output stages do something with it — starts
it, and runs until stopped.

Env vars:

- `PIPELINE` (default `"cnn"`) — which model family runs: `"cnn"` (the model this container
  actually deploys, see `src/cv-argus/CLAUDE.md`'s "Current status") or `"lstm"` (kept,
  optional; needs `MODEL_DRIVE_FILE_ID` set). `FaceDetectorCropStage` -> `CnnInferenceStage`, or
  `FaceLandmarkerStage` -> `LstmInferenceStage`.
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

Every pipeline shares the same `Source`/`Pipeline`/output-stage plumbing regardless of
`PIPELINE` — that sharing, not just the download logic, is the point of the `Stage` abstraction.
"""

import logging
import os
import signal
import threading

from cv_argus.model import CnnDrowsinessDetector, DrowsinessDetector
from cv_argus.pipeline import (
    CnnInferenceStage,
    FaceDetectorCropStage,
    FaceLandmarkerStage,
    LoggingOutputStage,
    LstmInferenceStage,
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


def _build_cnn_pipeline() -> Pipeline:
    bundle_path = download_face_detector_bundle()
    detector = CnnDrowsinessDetector.from_env()

    source = _build_source()
    crop_stage = FaceDetectorCropStage(bundle_path)
    inference_stage = CnnInferenceStage(detector)
    outputs = _build_outputs()

    source.connect(crop_stage).connect(inference_stage)
    for output in outputs:
        inference_stage.connect(output)
    return Pipeline([source, crop_stage, inference_stage, *outputs])


def _build_lstm_pipeline() -> Pipeline:
    bundle_path = download_face_landmarker_bundle()
    detector = DrowsinessDetector.from_env()

    source = _build_source()
    landmarker_stage = FaceLandmarkerStage(bundle_path)
    inference_stage = LstmInferenceStage(detector)
    outputs = _build_outputs()

    source.connect(landmarker_stage).connect(inference_stage)
    for output in outputs:
        inference_stage.connect(output)
    return Pipeline([source, landmarker_stage, inference_stage, *outputs])


_PIPELINE_BUILDERS = {
    "cnn": _build_cnn_pipeline,
    "lstm": _build_lstm_pipeline,
}


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    pipeline_name = os.environ.get("PIPELINE", "cnn").strip().lower()
    build = _PIPELINE_BUILDERS.get(pipeline_name)
    if build is None:
        raise SystemExit(
            f"Unknown PIPELINE={pipeline_name!r} -- expected one of {sorted(_PIPELINE_BUILDERS)}"
        )

    logger.info(
        "cv-argus starting (PIPELINE=%s, SOURCE=%s, OUTPUTS=%s)",
        pipeline_name,
        os.environ.get("SOURCE", "video_capture"),
        os.environ.get("OUTPUTS", "logging"),
    )
    pipeline = build()

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
