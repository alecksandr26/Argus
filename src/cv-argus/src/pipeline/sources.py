"""Concrete `SourceStage`s: where frames come from.

`VideoCaptureSource` wraps `cv2.VideoCapture`, which already accepts an int camera index, a
`/dev/videoN` device path, *or* a video file path — the same three-way convention the existing
`CAMERA_SOURCE` env var uses (see `.env.example`) — so this one class covers both "a camera"
and "a recorded video file" without needing two separate classes. `PiCameraSource` wraps
`picamera2` for the Raspberry Pi 5's CSI camera (see `src/cv-argus/CLAUDE.md`'s "Python
packaging" section for why `picamera2`, gated to arm platforms in `requirements.txt`/`setup.py`).

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

from .stage import FrameContext, SourceStage

logger = logging.getLogger(__name__)


def _is_live_source(source: int | str) -> bool:
    """True for a camera (an int index, or a `/dev/...` device path) — false for a video file.
    Drives `VideoCaptureSource`'s default `drop_oldest_when_full` policy: a live source should
    bound latency by dropping stale frames when downstream can't keep up; an offline file
    should process every frame losslessly instead, since there's no "real time" to keep up with.
    """
    return isinstance(source, int) or (isinstance(source, str) and source.startswith("/dev/"))


class VideoCaptureSource(SourceStage):
    """Wraps `cv2.VideoCapture`. `source` is whatever `CAMERA_SOURCE` already accepts: an int
    camera index, a `/dev/videoN` path, or a video file path.

    `target_fps`, if set, paces playback to roughly that rate — mainly useful for replaying a
    video file at a "live" cadence during testing; a real camera already produces frames at its
    own rate and doesn't need pacing.
    """

    def __init__(
        self,
        source: int | str,
        name: str = "video_capture",
        target_fps: float | None = None,
        **kwargs,
    ) -> None:
        kwargs.setdefault("drop_oldest_when_full", _is_live_source(source))
        super().__init__(name, **kwargs)
        self._source = source
        self._target_fps = target_fps
        self._cap: cv2.VideoCapture | None = None

    def produce(self) -> Iterator[FrameContext]:
        self._cap = cv2.VideoCapture(self._source)
        if not self._cap.isOpened():
            raise RuntimeError(f"Could not open video source: {self._source!r}")
        if _is_live_source(self._source):
            # Force MJPG on a live camera, set *before* touching resolution (order matters to
            # V4L2 -- a FourCC set after the resolution negotiation doesn't reliably take). Left
            # unset, a lot of UVC webcams fall back to raw YUYV, which either exceeds available
            # USB bandwidth or gets decoded wrong -- the textbook result is a solid green/
            # corrupted frame that still reads back as a "successful" cap.read(), so this can
            # look like "the camera is working" while there's no real picture in it, and
            # MediaPipe then correctly finds zero faces in a frame with no face in it. This bites
            # hardest on a USB webcam passed through `usbipd-win` into WSL2 (see the README's
            # Troubleshooting section), but is worth forcing unconditionally for any live camera,
            # not just that one case.
            self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*"MJPG"))
        try:
            frame_interval = 1.0 / self._target_fps if self._target_fps else None
            next_due = time.monotonic()
            while not self._stop_event.is_set():
                ok, frame_bgr = self._cap.read()
                if not ok:
                    logger.info("%s: source exhausted or disconnected (%r)", self.name, self._source)
                    break
                yield FrameContext(
                    frame_bgr=frame_bgr,
                    timestamp_ms=int(time.monotonic() * 1000),
                    source_id=self.name,
                )
                if frame_interval is not None:
                    next_due += frame_interval
                    sleep_for = next_due - time.monotonic()
                    if sleep_for > 0:
                        time.sleep(sleep_for)
        finally:
            self._cap.release()


class PiCameraSource(SourceStage):
    """CSI camera via `picamera2` — Raspberry Pi only. The `picamera2` import is deferred into
    `produce()` (not module level) so this module still imports cleanly on a dev laptop where
    `picamera2` isn't installed (see `requirements.txt`'s `platform_machine in 'armv7l aarch64'`
    gate) — importing this *class* is fine anywhere; only instantiating/running it needs the
    real hardware/library.

    Requests `BGR888` output specifically so `frame_bgr` matches `cv2.VideoCapture`'s channel
    order (the rest of the pipeline, e.g. `FaceDetectorCropStage`, assumes BGR) — `picamera2`
    supports several output pixel formats and this isn't necessarily its default.

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
        **kwargs,
    ) -> None:
        kwargs["drop_oldest_when_full"] = True
        super().__init__(name, **kwargs)
        self._resolution = resolution

    def produce(self) -> Iterator[FrameContext]:
        from picamera2 import Picamera2  # deferred -- see class docstring

        picam2 = Picamera2()
        config = picam2.create_video_configuration(
            main={"size": self._resolution, "format": "BGR888"}
        )
        picam2.configure(config)
        picam2.start()
        try:
            while not self._stop_event.is_set():
                frame_bgr = picam2.capture_array()
                yield FrameContext(
                    frame_bgr=frame_bgr,
                    timestamp_ms=int(time.monotonic() * 1000),
                    source_id=self.name,
                )
        finally:
            picam2.stop()
