"""Entry point for the cv-argus edge process.

Builds and starts **three independent peer components**, coupled only through `buffer/`'s
SQLite file (see the approved alert-pipeline plan's "three independent peers" decision):

1. **Pipeline** (`cv_argus.pipeline.stage`) — a `Source` stage reads frames, two MediaPipe
   stages produce a face crop and a geometric-feature vector from it, an inference stage turns
   both into a `DetectionResult`, and one or more output stages do something with it. There is
   one pipeline shape: `FaceDetectorCropStage` -> `FaceLandmarkerCropStage` ->
   `FusedInferenceStage` (the frozen-CNN-embedding + geometric-feature + LSTM classifier — see
   `src/cv-argus/CLAUDE.md`'s "Current status" for its measured accuracy and caveats). Earlier
   versions of this module supported a `PIPELINE` env var switching between this, a
   single-frame CNN, and a windowed-geometric-only LSTM — both were removed once this fused
   pipeline's result made them obsolete; see the root `CLAUDE.md` for why.
2. **Orchestrator** (`cv_argus.orchestrator`) — owns its own decision-loop thread, fed by a
   thin, always-on `OrchestratorBridgeOutputStage` sink wired into the pipeline above
   (structural, unlike the `OUTPUTS=` demo/observability toggles below). Decides whether a
   `DetectionResult` is worth raising an Alert over (debounce + cooldown) and emits a periodic
   RouteStatus("OK") heartbeat otherwise, enqueuing both into `buffer/`.
3. **Sender** (`cv_argus.sender`) — owns its own Bluetooth-accept-loop thread, answering the
   ESP32's PULL/ACK protocol against the same `buffer/` (see `sender/server.py`'s module
   docstring for the ack-only-marks-sent invariant).

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
- `BLUETOOTH_SECURITY` (default `"medium"`: paired + encrypted link required) — `low`/`medium`/
  `high`, the RFCOMM security level the listener demands. See `sender.bluetooth_transport`.
- `ESP32_BT_MAC` (unset by default) — comma-separated Bluetooth MACs allowed to connect; unset
  accepts any paired device.
- `ARGUS_TRUCK_ID`/`ARGUS_DEVICE_API_KEY`/`ARGUS_BACKEND_URL` (unset by default) — this truck's
  backend credentials, from the dashboard's `argus-device.env`. Served to the ESP32 on `CONFIG`;
  all three or none. See `cv_argus.sender.device_config`.
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
from cv_argus.orchestrator import Orchestrator, OrchestratorBridgeOutputStage
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
from cv_argus.sender import BluetoothSppListener, DeviceConfig, SenderServer
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
        security = os.environ.get("BLUETOOTH_SECURITY", "medium")
        try:
            return BluetoothSppListener(channel=channel, security=security)
        except ValueError as exc:
            raise SystemExit(f"BLUETOOTH_SECURITY: {exc}") from None
    if kind == "none":
        return None
    raise SystemExit(f"Unknown SENDER_TRANSPORT={kind!r} -- expected 'bluetooth' or 'none'")


def _device_config() -> DeviceConfig | None:
    """This truck's backend credentials (`ARGUS_*`, from the dashboard's `argus-device.env`),
    served to the ESP32 on `CONFIG`. A partial set stops startup rather than running a truck
    whose ESP32 can never authenticate."""
    try:
        config = DeviceConfig.from_env()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    if config is None:
        logger.warning(
            "No device credentials (ARGUS_TRUCK_ID/ARGUS_DEVICE_API_KEY/ARGUS_BACKEND_URL) -- "
            "the ESP32 can't reach the backend until argus-device.env is installed"
        )
    else:
        logger.info("Device credentials loaded for truck %s (%s)", config.truck_id, config.backend_url)
    return config


def _allowed_peers() -> list[str] | None:
    raw = os.environ.get("ESP32_BT_MAC", "").strip()
    return [mac.strip() for mac in raw.split(",") if mac.strip()] or None


def _build_pipeline(orchestrator: Orchestrator) -> Pipeline:
    """`orchestrator` supplies the queue `OrchestratorBridgeOutputStage` feeds — see the module
    docstring's "three independent peers" summary. The bridge is connected unconditionally,
    alongside (not through) the `OUTPUTS=`-driven sinks: it's structural, not a demo toggle."""
    face_detector_bundle = download_face_detector_bundle()
    face_landmarker_bundle = download_face_landmarker_bundle()
    detector = FusedDrowsinessDetector.from_env()

    source = _build_source()
    crop_stage = FaceDetectorCropStage(face_detector_bundle)
    landmarker_crop_stage = FaceLandmarkerCropStage(face_landmarker_bundle)
    inference_stage = FusedInferenceStage(detector)
    outputs = _build_outputs()
    bridge = OrchestratorBridgeOutputStage(orchestrator.input_queue)

    source.connect(crop_stage).connect(landmarker_crop_stage).connect(inference_stage)
    for output in outputs:
        inference_stage.connect(output)
    inference_stage.connect(bridge)
    return Pipeline(
        [source, crop_stage, landmarker_crop_stage, inference_stage, *outputs, bridge],
        stats_interval=_latency_log_interval(),
    )


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    logger.info(
        "cv-argus starting (SOURCE=%s, OUTPUTS=%s, SAMPLE_FPS=%s, SENDER_TRANSPORT=%s, "
        "LATENCY_LOG_INTERVAL=%s, CV_ARGUS_NUM_THREADS=%s)",
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
    sender = (
        SenderServer(
            buffer,
            listener,
            device_config=_device_config(),
            allowed_peers=_allowed_peers(),
        )
        if listener is not None
        else None
    )

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
