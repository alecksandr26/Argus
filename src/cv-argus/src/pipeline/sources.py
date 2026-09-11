"""Concrete `SourceStage`s: where frames come from.

`VideoCaptureSource` wraps `cv2.VideoCapture`, which already accepts an int camera index, a
`/dev/videoN` device path, *or* a video file path — the same three-way convention the existing
`CAMERA_SOURCE` env var uses (see `.env.example`) — so this one class covers both "a camera"
and "a recorded video file" without needing two separate classes. `PiCameraSource` wraps
`picamera2` for the Raspberry Pi 5's CSI camera (see `src/cv-argus/CLAUDE.md`'s "Python
packaging" section for why `picamera2`, gated to arm platforms in `requirements.txt`/`setup.py`).

**Frame-rate cap (`target_fps` / `frame_rate`, default `constants.DEFAULT_SAMPLE_FPS` = 5).**
The deployed model was trained on frames sampled at 5 fps, so the pipeline samples at that rate
too — and it does so *here*, at the source, so the skipped frames never cost a MediaPipe /
CNN / LSTM cycle downstream:

- a live camera is decimated with `cap.grab()` (pulls the next frame off the driver *without
  decoding* it — cheap) and only `cap.retrieve()`-decoded + emitted once `1/target_fps` has
  elapsed;
- a video file uses a fixed `frame_stride = round(src_fps / target_fps)` and emits a virtual
  frame-clock `timestamp_ms` (deterministic, strictly increasing — matches the notebooks);
- `PiCameraSource` sets the sensor's `FrameRate` control directly, so the CSI camera reads out
  at 5 fps and the ISP never does the work for the other frames.

Pass `target_fps=0` / `None` to disable the cap (every frame — the old behaviour).

"Multiple cameras" is multiple `Pipeline` instances, each owning its own `Source` stage — not
one `Source` reading from more than one camera. Merging independent camera feeds through a
single detector would conflate their timestamps and identity for no benefit Argus's actual use
case (one driver-facing camera) needs; `Stage.connect()`'s fan-out already covers the more
likely near-term need (running two model families off the same one camera).
"""

import logging
import time
from collections.abc import Iterator

import cv2

from .. import constants
from .stage import FrameContext, SourceStage

logger = logging.getLogger(__name__)


def _is_live_source(source: int | str) -> bool:
    """True for a camera (an int index, or a `/dev/...` device path) — false for a video file.
    Drives `VideoCaptureSource`'s default `drop_oldest_when_full` policy and which sampling
    strategy `produce()` uses: a live source paces by wall-clock and bounds latency by dropping
    stale frames; an offline file strides by frame count and processes every kept frame
    losslessly, since there's no "real time" to keep up with.
    """
    return isinstance(source, int) or (isinstance(source, str) and source.startswith("/dev/"))


def _frame_stride(src_fps: float, target_fps: float | None) -> int:
    """`round(src_fps / target_fps)`, clamped to at least 1. Returns 1 (keep every frame) when
    either rate is missing or non-positive. Matches
    `src/dataset/argus_dataset/pipelines.py`'s `frame_stride` for video-file decimation."""
    if not target_fps or target_fps <= 0 or not src_fps or src_fps <= 0:
        return 1
    return max(1, round(src_fps / target_fps))


class VideoCaptureSource(SourceStage):
    """Wraps `cv2.VideoCapture`. `source` is whatever `CAMERA_SOURCE` already accepts: an int
    camera index, a `/dev/videoN` path, or a video file path.

    `target_fps` caps the emitted frame rate (see the module docstring); it defaults to
    `constants.DEFAULT_SAMPLE_FPS` (5, the model's training rate). `0`/`None` emits every frame.
    """

    def __init__(
        self,
        source: int | str,
        name: str = "video_capture",
        target_fps: float | None = constants.DEFAULT_SAMPLE_FPS,
        **kwargs,
    ) -> None:
        kwargs.setdefault("drop_oldest_when_full", _is_live_source(source))
        super().__init__(name, **kwargs)
        self._source = source
        self._target_fps = target_fps or None
        self._cap: cv2.VideoCapture | None = None

    def produce(self) -> Iterator[FrameContext]:
        self._cap = cv2.VideoCapture(self._source)
        if not self._cap.isOpened():
            raise RuntimeError(f"Could not open video source: {self._source!r}")
        try:
            if _is_live_source(self._source):
                # Force MJPG on a live camera, set *before* touching anything else (order
                # matters to V4L2 -- a FourCC set later doesn't reliably take). Left unset, a
                # lot of UVC webcams fall back to raw YUYV, which either exceeds USB bandwidth
                # or decodes wrong -- the textbook result is a solid green/corrupted frame that
                # still reads back as a "successful" grab, so it can look like the camera works
                # while there's no real picture in it and MediaPipe correctly finds zero faces.
                # Worst on a webcam passed through usbipd-win into WSL2 (see the README), but
                # worth forcing for any live camera.
                self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*"MJPG"))
                # The camera stays at its native rate; `_produce_live` grabs every frame (cheap
                # -- no decode) and only decodes/emits at `target_fps`. A `CAP_PROP_FPS` hint is
                # deliberately *not* set: several UVC drivers over- or under-clamp it, which
                # then fights the pacing loop -- native rate + grab-skip is predictable.
                yield from self._produce_live()
            else:
                yield from self._produce_file()
        finally:
            self._cap.release()

    def _produce_live(self) -> Iterator[FrameContext]:
        interval = 1.0 / self._target_fps if self._target_fps else 0.0
        epoch = time.monotonic()
        emitted = 0  # frames emitted so far; the target is one per `interval` since `epoch`
        while not self._stop_event.is_set():
            if not self._cap.grab():  # advance the camera, no decode
                logger.info("%s: source disconnected (%r)", self.name, self._source)
                break
            now = time.monotonic()
            due = int((now - epoch) / interval) if interval else emitted
            if interval and due < emitted:
                continue  # on schedule already -- this grabbed frame is a skip
            read_start = time.monotonic()
            ok, frame_bgr = self._cap.retrieve()  # decode only the frame we're keeping
            self.stats.record_process(time.monotonic() - read_start)
            if not ok:
                continue
            # Next target is `due + 1` (paced off a fixed epoch, so grab jitter doesn't drift);
            # after a stall this jumps past the backlog so the pipeline gets a fresh frame, not
            # a burst of stale ones.
            emitted = due + 1 if interval else emitted + 1
            yield FrameContext(
                frame_bgr=frame_bgr,
                timestamp_ms=int(now * 1000),
                source_id=self.name,
            )

    def _produce_file(self) -> Iterator[FrameContext]:
        src_fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
        stride = _frame_stride(src_fps, self._target_fps)
        frame_idx = 0
        while not self._stop_event.is_set():
            if not self._cap.grab():
                logger.info("%s: source exhausted (%r)", self.name, self._source)
                break
            if frame_idx % stride == 0:
                read_start = time.monotonic()
                ok, frame_bgr = self._cap.retrieve()
                self.stats.record_process(time.monotonic() - read_start)
                if ok:
                    yield FrameContext(
                        frame_bgr=frame_bgr,
                        # Virtual frame-clock: deterministic and strictly increasing regardless
                        # of how fast the file is read (wall-clock would be replay-speed
                        # dependent), and MediaPipe's VIDEO mode needs monotonic timestamps.
                        timestamp_ms=int(frame_idx * 1000.0 / src_fps),
                        source_id=self.name,
                    )
            frame_idx += 1


class PiCameraSource(SourceStage):
    """CSI camera via `picamera2` — Raspberry Pi only. The `picamera2` import is deferred into
    `produce()` (not module level) so this module still imports cleanly on a dev laptop where
    `picamera2` isn't installed (see `requirements.txt`'s `platform_machine in 'armv7l aarch64'`
    gate) — importing this *class* is fine anywhere; only instantiating/running it needs the
    real hardware/library.

    Requests `BGR888` output specifically so `frame_bgr` matches `cv2.VideoCapture`'s channel
    order (the rest of the pipeline, e.g. `FaceDetectorCropStage`, assumes BGR) — `picamera2`
    supports several output pixel formats and this isn't necessarily its default.

    `frame_rate` (default `constants.DEFAULT_SAMPLE_FPS` = 5) is applied as the sensor's
    `FrameRate` control, so the camera reads out at that rate and the ISP does no work for
    frames the pipeline would skip anyway. `0`/`None` leaves the sensor at its default rate.

    A CSI camera is always a live source, so `drop_oldest_when_full` is always `True` here
    (unlike `VideoCaptureSource`, there's no "it might be a file" case for this class).

    Not yet verified against real Pi hardware in this repo — see `src/cv-argus/CLAUDE.md`'s
    "Open decisions": `picamera2` inside a container has its own device-passthrough
    requirements (typically `/dev/video*` plus `/dev/dma_heap/*`) that still need a dedicated
    smoke test on a real Pi 5.
    """

    def __init__(
        self,
        name: str = "pi_camera",
        resolution: tuple[int, int] = (640, 480),
        frame_rate: float | None = constants.DEFAULT_SAMPLE_FPS,
        **kwargs,
    ) -> None:
        kwargs["drop_oldest_when_full"] = True
        super().__init__(name, **kwargs)
        self._resolution = resolution
        self._frame_rate = frame_rate or None

    def produce(self) -> Iterator[FrameContext]:
        from picamera2 import Picamera2  # deferred -- see class docstring

        picam2 = Picamera2()
        controls = {"FrameRate": float(self._frame_rate)} if self._frame_rate else {}
        config = picam2.create_video_configuration(
            main={"size": self._resolution, "format": "BGR888"}, controls=controls
        )
        picam2.configure(config)
        picam2.start()
        try:
            while not self._stop_event.is_set():
                capture_start = time.monotonic()
                frame_bgr = picam2.capture_array()
                self.stats.record_process(time.monotonic() - capture_start)  # frame-grab cost
                yield FrameContext(
                    frame_bgr=frame_bgr,
                    timestamp_ms=int(time.monotonic() * 1000),
                    source_id=self.name,
                )
        finally:
            picam2.stop()
