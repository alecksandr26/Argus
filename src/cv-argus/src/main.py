"""Entry point for the cv-argus edge process.

Builds and starts **three independent peer components**, coupled only through `buffer/`'s
SQLite file (see the approved alert-pipeline plan's "three independent peers" decision):

1. **Pipeline** (`cv_argus.pipeline.stage`) — a `Source` stage reads frames, BlazeFace
   (`FaceDetectorCropStage`) finds the face, the `PIPELINE`-selected model stages turn it into a
   `DetectionResult`, and one or more output stages do something with it:
   - `PIPELINE=fused` (default): `FaceLandmarkerCropStage` -> `FusedInferenceStage`, the
     frozen-CNN-embedding + geometric-feature + LSTM classifier. See `src/cv-argus/CLAUDE.md`'s
     "Current status" for its measured accuracy and caveats.
   - `PIPELINE=parts`: `PartsInferenceStage`. It runs notebook 12's tiny YOLO-style detector
     (eyes open/closed, yawn, head straight/down) on a square gray face crop, then an IoU
     tracker, then deterministic rules with timers and severities
     (`orchestrator/parts_rules.py`).
2. **Orchestrator** (`cv_argus.orchestrator`) — owns its own decision-loop thread, fed by a
   thin, always-on `OrchestratorBridgeOutputStage` sink wired into the pipeline above
   (structural, unlike the `OUTPUTS=` demo/observability toggles below). Turns `DetectionResult`s
   into debounced class *readings* (one per change plus keepalives) and a periodic
   RouteStatus("OK") heartbeat, enqueuing both into `buffer/`.
3. **Sender** (`cv_argus.sender`) — owns its own Bluetooth-accept-loop thread, answering the
   ESP32's PULL/ACK protocol against the same `buffer/` (see `sender/server.py`'s module
   docstring for the ack-only-marks-sent invariant).

Env vars:

- `PIPELINE` (default `"fused"`) — `"fused"` or `"parts"`, see above.
- `PARTS_PRECISION` (default `"int8"`) — only for `PIPELINE=parts`: `"int8"` (TFLite) or
  `"float"` (`.keras`), the same detector at two precisions. Rule thresholds are `RULE_*` env
  vars; see `orchestrator/parts_rules.py` and `constants.py`.
- `DEMO_STREAM_WIDTH` (unset) — downscale the MJPEG demo stream to this width. Boxes are drawn
  first at native resolution, so they stay aligned.
- `SOURCE` (default `"video_capture"`) — where frames come from: `"video_capture"`
  (`cv2.VideoCapture`, driven by `CAMERA_SOURCE` — a camera index, a `/dev/videoN` path, or a
  video file) or `"picamera"` (the Pi 5's CSI camera via `picamera2`).
- `OUTPUTS` (default `"logging"`) — comma-separated list of sinks to attach, fanned out from the
  same inference stage via `Stage.connect()` (see `pipeline/stage.py`) — no new plumbing needed
  to run more than one at once. `"logging"` (`LoggingOutputStage`, text) and/or `"mjpeg"`
  (`MjpegStreamOutputStage`, a browser-viewable annotated video stream — demo-only, see that
  module's docstring for why it isn't on by default: no authentication, and this project's own
  stated cargo-theft/security risk model makes an open camera stream a real exposure to leave
  running). The `orchestrator/` bridge stage is *not* one of these — it's always attached.
- `DEMO_STREAM_HOST`/`DEMO_STREAM_PORT` (default `"0.0.0.0"`/`8080`) — only read if `OUTPUTS`
  includes `"mjpeg"`.
- `LOG_LEVEL` (default `"INFO"`) — root log level. `DEBUG` also surfaces `LoggingOutputStage`'s
  per-frame lines and the drop-oldest debug logs.
- `LATENCY_LOG_INTERVAL` (default `"10"`) — seconds between per-stage `StageStats` report
  lines (timing, input-queue depth, dropped-frame count, end-to-end latency at the sink — see
  `cv_argus.pipeline.latency`). `0` disables them.
- `SAMPLE_FPS` (default `constants.DEFAULT_SAMPLE_FPS` = 5) — frames/sec sampled from the
  camera, at the source (skipped frames aren't even decoded). 5 matches the model's training
  rate; `0` processes every frame. See `cv_argus.pipeline.sources`.
- `CV_ARGUS_NUM_THREADS` (unset by default) — pins BLAS/OpenMP/TensorFlow thread pools; set by
  `docker-compose.yml` to 4 to simulate the Raspberry Pi 5. See `cv_argus.bootstrap`.
- `BUFFER_DIR`/`BUFFER_DB_FILENAME` (defaults `constants.BUFFER_DIR_DEFAULT`/
  `BUFFER_DB_FILENAME_DEFAULT`) — where `buffer/`'s SQLite file lives. See
  `cv_argus.buffer.store`.
- `SENDER_TRANSPORT` (default `"bluetooth"`) — `"bluetooth"` (real `BluetoothSppListener`,
  `BLUETOOTH_CHANNEL` env var, default `4`) or `"none"` (no `SenderServer` at all — a dev/CI
  opt-out for a machine with no Bluetooth adapter). See `cv_argus.sender`.
"""

import cv_argus.bootstrap  # noqa: F401 -- MUST be first: sets thread env vars before tf/cv2 load

import logging
import os
import signal
import sys
import threading

from cv_argus import constants
from cv_argus.buffer import open_buffer
from cv_argus.model import FusedDrowsinessDetector
from cv_argus.model.parts_detector import PartsDetector
from cv_argus.orchestrator import Orchestrator, OrchestratorBridgeOutputStage
from cv_argus.pipeline import (
    FaceDetectorCropStage,
    FaceLandmarkerCropStage,
    FusedInferenceStage,
    LoggingOutputStage,
    MetricsLogOutputStage,
    MjpegStreamOutputStage,
    OutputStage,
    PartsInferenceStage,
    PiCameraSource,
    Pipeline,
    SourceStage,
    VideoCaptureSource,
    download_face_detector_bundle,
    download_face_landmarker_bundle,
)
from cv_argus.sender import BluetoothSppListener, SenderServer
from cv_argus.supervisor import exit_code, first_dead

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


def _sample_fps() -> float:
    """Frames/sec to sample from the camera (`SAMPLE_FPS`, default
    `constants.DEFAULT_SAMPLE_FPS`; `0` = uncapped, process every frame). A malformed value
    falls back to the default rather than aborting startup."""
    raw = os.environ.get("SAMPLE_FPS", str(constants.DEFAULT_SAMPLE_FPS)).strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        logger.warning("SAMPLE_FPS=%r is not a number, using %s", raw, constants.DEFAULT_SAMPLE_FPS)
        return float(constants.DEFAULT_SAMPLE_FPS)


def _build_source() -> SourceStage:
    kind = os.environ.get("SOURCE", "video_capture").strip().lower()
    fps = _sample_fps()
    if kind == "video_capture":
        return VideoCaptureSource(_camera_source(), target_fps=fps)
    if kind == "picamera":
        return PiCameraSource(frame_rate=fps)
    raise SystemExit(f"Unknown SOURCE={kind!r} -- expected 'video_capture' or 'picamera'")


def _stream_width() -> int | None:
    raw = os.environ.get("DEMO_STREAM_WIDTH", "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


def _camera_label() -> str:
    if os.environ.get("SOURCE", "video_capture").strip().lower() == "picamera":
        return "picam"
    return os.path.basename(str(_camera_source()))


_OUTPUT_BUILDERS = {
    "logging": lambda: LoggingOutputStage(),
    "metrics": lambda: MetricsLogOutputStage(),
    "mjpeg": lambda: MjpegStreamOutputStage(
        host=os.environ.get("DEMO_STREAM_HOST", "0.0.0.0"),
        port=int(os.environ.get("DEMO_STREAM_PORT", "8080")),
        width=_stream_width(),
        camera_label=_camera_label(),
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


def _build_sender_listener():
    """`SENDER_TRANSPORT` (default `"bluetooth"`) picks the `Listener` `SenderServer` accepts
    connections on: `"bluetooth"` (`BluetoothSppListener`, `BLUETOOTH_CHANNEL` env var, default
    `4`) or `"none"` (an explicit dev/CI opt-out — no `SenderServer` is started at all, e.g. on a
    machine with no Bluetooth adapter). Returns `None` for `"none"`; `main()` treats that as
    "don't start sender/ this run". Real Bluetooth container passthrough (`/dev/rfcommN` or the
    host BlueZ socket) still isn't wired into `docker-compose.yml` — see
    `src/cv-argus/CLAUDE.md`'s "Open decisions"."""
    kind = os.environ.get("SENDER_TRANSPORT", "bluetooth").strip().lower()
    if kind == "bluetooth":
        channel = int(os.environ.get("BLUETOOTH_CHANNEL", "4"))
        return BluetoothSppListener(channel=channel)
    if kind == "none":
        return None
    raise SystemExit(f"Unknown SENDER_TRANSPORT={kind!r} -- expected 'bluetooth' or 'none'")


def _fused_stages() -> list:
    """BlazeFace -> FaceLandmarker (on the crop) -> frozen-CNN-embedding + geometric + LSTM."""
    face_landmarker_bundle = download_face_landmarker_bundle()
    return [
        FaceLandmarkerCropStage(face_landmarker_bundle),
        FusedInferenceStage(FusedDrowsinessDetector.from_env()),
    ]


def _parts_stages() -> list:
    """BlazeFace -> square gray crop -> tiny YOLO (int8|float) -> tracker -> rule engine."""
    return [PartsInferenceStage(PartsDetector.from_env())]


_PIPELINE_BUILDERS = {"fused": _fused_stages, "parts": _parts_stages}


def _pipeline_name() -> str:
    name = os.environ.get("PIPELINE", "fused").strip().lower() or "fused"
    if name not in _PIPELINE_BUILDERS:
        raise SystemExit(f"Unknown PIPELINE={name!r} -- expected one of {sorted(_PIPELINE_BUILDERS)}")
    return name


def _build_pipeline(orchestrator: Orchestrator) -> Pipeline:
    """`orchestrator` supplies the queue `OrchestratorBridgeOutputStage` feeds — see the module
    docstring's "three independent peers" summary. The bridge is connected unconditionally,
    alongside (not through) the `OUTPUTS=`-driven sinks: it's structural, not a demo toggle.

    `PIPELINE` picks what runs after the shared BlazeFace stage (see `_PIPELINE_BUILDERS`); both
    end in a stage that sets `ctx.detection`, so outputs and the orchestrator are identical."""
    face_detector_bundle = download_face_detector_bundle()
    model_stages = _PIPELINE_BUILDERS[_pipeline_name()]()

    source = _build_source()
    crop_stage = FaceDetectorCropStage(face_detector_bundle)
    outputs = _build_outputs()
    bridge = OrchestratorBridgeOutputStage(orchestrator.input_queue)

    last = source.connect(crop_stage)
    for stage in model_stages:
        last = last.connect(stage)
    for output in outputs:
        last.connect(output)
    last.connect(bridge)
    return Pipeline(
        [source, crop_stage, *model_stages, *outputs, bridge],
        stats_interval=_latency_log_interval(),
    )


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    logger.info(
        "cv-argus starting (PIPELINE=%s, PARTS_PRECISION=%s, SOURCE=%s, OUTPUTS=%s, SAMPLE_FPS=%s, SENDER_TRANSPORT=%s, "
        "LATENCY_LOG_INTERVAL=%s, CV_ARGUS_NUM_THREADS=%s)",
        os.environ.get("PIPELINE", "fused"),
        os.environ.get("PARTS_PRECISION", constants.PARTS_DEFAULT_PRECISION),
        os.environ.get("SOURCE", "video_capture"),
        os.environ.get("OUTPUTS", "logging"),
        os.environ.get("SAMPLE_FPS", str(constants.DEFAULT_SAMPLE_FPS)),
        os.environ.get("SENDER_TRANSPORT", "bluetooth"),
        os.environ.get("LATENCY_LOG_INTERVAL", "10"),
        os.environ.get("CV_ARGUS_NUM_THREADS", "(unset)"),
    )

    buffer = open_buffer()
    orchestrator = Orchestrator(buffer)
    pipeline = _build_pipeline(orchestrator)
    listener = _build_sender_listener()
    sender = SenderServer(buffer, listener) if listener is not None else None

    stop_event = threading.Event()
    dead: str | None = None

    def _handle_signal(signum, _frame) -> None:
        logger.info("Received signal %s, shutting down...", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    orchestrator.start()
    pipeline.start()
    if sender is not None:
        sender.start()
    else:
        logger.warning("SENDER_TRANSPORT=none -- alerts will accumulate in buffer/ unsent")
    try:
        # Wake up periodically rather than blocking on stop_event forever: a video-file source
        # reaching EOF stops the pipeline's own threads without ever setting stop_event, and
        # this is what notices that and lets the process exit instead of hanging.
        components = {"pipeline": pipeline, "orchestrator": orchestrator, "sender": sender}
        while not stop_event.is_set() and (dead := first_dead(components)) is None:
            stop_event.wait(timeout=1.0)
    finally:
        logger.info("Stopping...")
        # Shutdown order matters: pipeline first (stops producing detections) -> orchestrator
        # (stops producing new Alerts/RouteStatus into the buffer) -> sender (stops reading/
        # acking the buffer) -> the buffer connection itself last, once both of its users are
        # confirmed joined. Each step only stops the thing that *feeds* the next, so nothing is
        # asked to read/write a resource that's already torn down.
        pipeline.stop()
        orchestrator.stop()
        orchestrator.join(timeout=5.0)
        if sender is not None:
            sender.stop()
            sender.join(timeout=5.0)
        buffer.close()
        logger.info("cv-argus stopped")
    if dead is not None and dead != "pipeline":
        logger.error("cv-argus: %s died; exiting so the container restarts", dead)
    code = exit_code(stop_requested=stop_event.is_set(), dead=None if dead == "pipeline" else dead)
    if code:
        sys.exit(code)


if __name__ == "__main__":
    main()
