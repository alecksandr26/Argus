"""`MjpegStreamOutputStage`: draws a live overlay (drowsiness class, face box, fps) onto each
frame and serves it as an MJPEG stream over plain HTTP — viewable from any device's browser on
the same network (`http://<host>:<port>/stream`), with no display attached to whatever machine
actually runs the pipeline, and no need for non-headless OpenCV or X11 passthrough in the
container (the Dockerfile deliberately uses `opencv-python-headless`).

Stdlib only (`http.server`, `socketserver`, `threading`) — no new pip dependency.

**Security note, stated plainly rather than glossed over: this stream has no authentication.**
The root `CLAUDE.md` already frames cargo-theft/security risk as a real concern for this
project (a stopped, monitored truck is a target) — an open, unauthenticated live feed of the
cabin is exactly the kind of exposure that risk model warns about. This stage is demo-only,
opt-in via `OUTPUTS` (see `src/main.py`), and should not be left enabled in a real deployment.
"""

import logging
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import cv2

from .stage import FrameContext, OutputStage

logger = logging.getLogger(__name__)

_BOUNDARY = b"frame"

# Alert -> green, Low Vigilant -> amber, Drowsy -> red. Falls back to white for anything
# unrecognized (e.g. DetectionResult.class_name's "Unknown(n)" case) rather than raising.
_STATUS_COLORS = {
    "Alert": (0, 200, 0),
    "Low Vigilant": (0, 165, 255),
    "Drowsy": (0, 0, 255),
}
_DEFAULT_COLOR = (255, 255, 255)


def draw_detection_overlay(frame_bgr, ctx: FrameContext, fps: float) -> None:
    """Draw the face box (if `ctx.features["face_bbox"]` is present), a color-coded status
    line, and an fps counter directly onto `frame_bgr` (mutated in place, not copied).

    Deliberately a standalone function, not a method on `MjpegStreamOutputStage` — so a future
    output stage (e.g. "save the annotated demo to a video file") can reuse the exact same
    overlay without depending on this module's HTTP-serving machinery.
    """
    bbox = ctx.features.get("face_bbox")
    label = ctx.detection.class_name if ctx.detection is not None else "No detection"
    color = _STATUS_COLORS.get(label, _DEFAULT_COLOR)

    if bbox is not None:
        x0, y0, x1, y1 = bbox
        cv2.rectangle(frame_bgr, (x0, y0), (x1, y1), color, 2)

    cv2.putText(frame_bgr, f"STATUS: {label}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
    cv2.putText(
        frame_bgr, f"fps: {fps:.1f}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1
    )


class _FrameBroadcaster:
    """Thread-safe holder for "the latest annotated JPEG frame".

    A live view only ever needs "now", not history, so this is a single-slot broadcast (with a
    version counter so a waiting connection can tell "new" from "same one again") rather than a
    `queue.Queue` — `queue.Queue` is the right tool for the pipeline's own internal
    stage-to-stage handoff (see `pipeline/stage.py`), a genuinely different concern: there,
    every item matters and order matters; here, only the newest frame ever matters, and a
    slow/absent viewer should never make the stage's own `handle()` block.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._latest_jpeg: bytes | None = None
        self._version = 0

    def publish(self, jpeg_bytes: bytes) -> None:
        with self._condition:
            self._latest_jpeg = jpeg_bytes
            self._version += 1
            self._condition.notify_all()

    def wait_for_next(self, last_seen_version: int, timeout: float = 5.0):
        """Block until a frame newer than `last_seen_version` is published, or `timeout`
        elapses. Returns `(jpeg_bytes, version)`, or `None` on timeout (the caller should just
        call again — this only exists so a connection with no traffic yet doesn't block
        forever and can still notice the server shutting down)."""
        with self._condition:
            got = self._condition.wait_for(
                lambda: self._version != last_seen_version, timeout=timeout
            )
            if not got or self._latest_jpeg is None:
                return None
            return self._latest_jpeg, self._version


class _MjpegRequestHandler(BaseHTTPRequestHandler):
    """Serves `GET /` or `GET /stream` as a `multipart/x-mixed-replace` MJPEG stream — the
    standard way to serve "video" over plain HTTP, rendered natively by any browser via
    `<img src=".../stream">` or by just navigating to the URL. Anything else 404s.

    Reads the broadcaster off `self.server.broadcaster` (set by `MjpegStreamOutputStage`) since
    `BaseHTTPRequestHandler` subclasses are instantiated per-request with fixed constructor
    args — this is the standard idiom for handing a handler shared state, not a workaround.
    """

    def do_GET(self) -> None:
        if self.path not in ("/", "/stream"):
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={_BOUNDARY.decode()}")
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Pragma", "no-cache")
        self.end_headers()

        last_version = 0
        try:
            while True:
                result = self.server.broadcaster.wait_for_next(last_version)
                if result is None:
                    continue  # no new frame yet (or none at all so far) -- keep waiting
                frame, last_version = result
                self.wfile.write(b"--" + _BOUNDARY + b"\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii"))
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass  # the viewer closed the tab/connection -- not an error

    def log_message(self, format: str, *args) -> None:
        # BaseHTTPRequestHandler logs every request to stderr by default; a long-lived
        # multipart stream would otherwise spam nothing (it's one request), but a page reload
        # from several viewers over the course of a demo doesn't need to fill the log either.
        logger.debug("%s - %s", self.address_string(), format % args)


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True  # per-connection threads never outlive the process on their own


class MjpegStreamOutputStage(OutputStage):
    """Draws `draw_detection_overlay()` onto each frame, JPEG-encodes it, and publishes it to
    an `_FrameBroadcaster` served over HTTP by a `_ThreadingHTTPServer` — supports more than one
    simultaneous viewer for free, since each connection gets its own thread.

    `host`/`port` are plain constructor arguments, not read from the environment here — same
    convention as every other stage in this package; `src/main.py` is what reads
    `DEMO_STREAM_HOST`/`DEMO_STREAM_PORT` and passes them in.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8080,
        jpeg_quality: int = 80,
        name: str = "mjpeg_stream",
        **kwargs,
    ) -> None:
        super().__init__(name, **kwargs)
        self._jpeg_quality = jpeg_quality
        self._broadcaster = _FrameBroadcaster()
        self._fps = 0.0
        self._last_frame_time: float | None = None

        self._server = _ThreadingHTTPServer((host, port), _MjpegRequestHandler)
        self._server.broadcaster = self._broadcaster
        self._server_thread = threading.Thread(
            target=self._server.serve_forever, name=f"{name}_http", daemon=True
        )
        self._server_thread.start()
        logger.info("%s: serving MJPEG stream at http://%s:%d/stream", name, host, port)

    def handle(self, ctx: FrameContext) -> None:
        now = time.monotonic()
        if self._last_frame_time is not None:
            dt = now - self._last_frame_time
            if dt > 0:
                self._fps = 0.9 * self._fps + 0.1 * (1.0 / dt)  # smoothed, not instantaneous
        self._last_frame_time = now

        frame = ctx.frame_bgr.copy()  # don't mutate what other output stages might still see
        draw_detection_overlay(frame, ctx, self._fps)
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality])
        if ok:
            self._broadcaster.publish(encoded.tobytes())
        else:
            logger.warning("%s: failed to JPEG-encode a frame, skipping", self.name)

    def close(self) -> None:
        self._server.shutdown()
        self._server_thread.join(timeout=2.0)
