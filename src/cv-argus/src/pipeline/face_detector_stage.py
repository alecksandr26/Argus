"""`FaceDetectorCropStage`: MediaPipe Face Detector (BlazeFace, short_range) -> the driver's face
box, feeding `PartsInferenceStage`. Detection follows `notebook/06_dataset_creation_face_crops.ipynb`
and the parts dataset builder (`VIDEO` running mode, `min_detection_confidence`), because the
parts detector was trained on crops built from exactly this detector's boxes. It publishes the raw
box (`face_bbox_raw`, which `PartsInferenceStage` turns into the square training crop) and a
margin-expanded box for the overlay (`face_bbox`).

**One face** (`FaceLock`, contracts C1.7). A cab can hold a passenger, or someone leaning in at a
stop, and only the driver counts:
- With no lock, the **largest** face is locked (the one nearest the camera: the driver).
- After that, only the locked face is reported. A detection is the locked face when it's close
  to the locked box (centre and size, see `FACE_LOCK_*`); the lock follows it frame by frame.
  Every other face is ignored.
- When the locked face is missing, the frame reads `face_found = False` (the rule engine counts
  no-face time), even if another face is visible.
- **Switch after `FACE_SWITCH_AFTER_S`.** The switch timer runs only while the locked face is
  missing **and** another face is continuously visible; it resets when the locked face returns
  or no other face is visible. On the switch frame (timer `>= FACE_SWITCH_AFTER_S`, on the frame
  timestamps the rule engine uses) the stage still reports `face_found = False` and sets
  `ctx.features["face_switched"] = True`; the lock moves to the largest visible face, which is
  used from the next frame. `PartsInferenceStage` then resets the rule timers and the tracker
  (never the held severity).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .. import constants
from .stage import FrameContext, Stage

logger = logging.getLogger(__name__)

Box = tuple[int, int, int, int]     # raw BlazeFace box: (origin_x, origin_y, width, height)


def _expand_and_clip_bbox(
    x: int, y: int, w: int, h: int, frame_w: int, frame_h: int, margin_frac: float
) -> tuple[int, int, int, int]:
    """Ported verbatim from notebook 06's `_expand_and_clip_bbox`: expand the raw detection
    bounding box by `margin_frac` on each side, then clip to the frame bounds."""
    mx, my = int(w * margin_frac), int(h * margin_frac)
    x0 = max(0, x - mx)
    y0 = max(0, y - my)
    x1 = min(frame_w, x + w + mx)
    y1 = min(frame_h, y + h + my)
    return x0, y0, x1, y1


def _area(box: Box) -> float:
    return float(box[2]) * float(box[3])


def _largest(boxes: list[Box]) -> Box:
    return max(boxes, key=_area)


@dataclass
class FaceLockResult:
    """One frame's verdict. `box` is the locked face's raw box, or `None` when it isn't seen."""
    box: Box | None
    faces: int                       # faces detected this frame (locked one included)
    switched: bool = False           # this is the switch frame
    switch_elapsed_s: float | None = None   # switch timer, while it runs

    @property
    def ignored(self) -> int:
        return self.faces - (1 if self.box is not None else 0)


class FaceLock:
    """Pure (no MediaPipe) "largest face, then sticky" selection with the timed switch described
    in the module docstring. `update(t, boxes)` takes the frame time in seconds and every face
    box detected on that frame."""

    def __init__(self, switch_after_s: float = constants.FACE_SWITCH_AFTER_S,
                 max_shift_frac: float = constants.FACE_LOCK_MAX_SHIFT_FRAC,
                 max_scale: float = constants.FACE_LOCK_MAX_SCALE) -> None:
        self._switch_after = switch_after_s
        self._max_shift = max_shift_frac
        self._max_scale = max_scale
        self._box: Box | None = None
        self._switch_since: float | None = None

    @property
    def locked_box(self) -> Box | None:
        return self._box

    def reset(self) -> None:
        self._box, self._switch_since = None, None

    def _match(self, boxes: list[Box]) -> int | None:
        """Index of the detection that is the locked face (the closest centre within tolerance),
        or `None`."""
        lx, ly, lw, lh = self._box
        side = max(lw, lh, 1)
        lcx, lcy = lx + lw / 2, ly + lh / 2
        best, best_d = None, None
        for k, (x, y, w, h) in enumerate(boxes):
            scale = max(w, h, 1) / side
            if not (1 / self._max_scale <= scale <= self._max_scale):
                continue
            d = ((x + w / 2 - lcx) ** 2 + (y + h / 2 - lcy) ** 2) ** 0.5
            if d <= self._max_shift * side and (best_d is None or d < best_d):
                best, best_d = k, d
        return best

    def update(self, t: float, boxes: list[Box]) -> FaceLockResult:
        boxes = list(boxes)
        if self._box is None:
            self._switch_since = None
            if not boxes:
                return FaceLockResult(None, 0)
            self._box = _largest(boxes)
            return FaceLockResult(self._box, len(boxes))

        k = self._match(boxes)
        if k is not None:
            self._box, self._switch_since = boxes[k], None
            return FaceLockResult(self._box, len(boxes))

        # The locked face is missing.
        if not boxes:
            self._switch_since = None
            return FaceLockResult(None, 0)
        if self._switch_since is None:
            self._switch_since = t
        elapsed = t - self._switch_since
        if elapsed >= self._switch_after:
            new_box = _largest(boxes)
            logger.info("face lock: locked face gone %.1f s with another face in view; switching to "
                        "the largest face %s", elapsed, new_box)
            self._box, self._switch_since = new_box, None
            return FaceLockResult(None, len(boxes), switched=True, switch_elapsed_s=elapsed)
        return FaceLockResult(None, len(boxes), switch_elapsed_s=elapsed)


class FaceDetectorCropStage(Stage):
    """Runs MediaPipe's Face Detector on each frame and records the locked face's box (see
    `FaceLock`). Frames without the locked face are passed through with `face_found=False`
    rather than dropped — `PartsInferenceStage` (whose rules count "no face" time) and the outputs
    decide what that means; this stage only reports it.

    `detector` injects any object with MediaPipe's `detect_for_video(image, ts_ms)` / `close()`
    (tests); by default one is built from `model_path`.
    """

    def __init__(
        self,
        model_path=None,
        name: str = "face_detector_crop",
        min_detection_confidence: float = constants.FACE_DETECTOR_MIN_DETECTION_CONFIDENCE,
        bbox_margin_frac: float = constants.FACE_DETECTOR_BBOX_MARGIN_FRAC,
        detector=None,
        face_lock: FaceLock | None = None,
        **kwargs,
    ) -> None:
        super().__init__(name, **kwargs)
        if detector is None:
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision

            base_options = mp_python.BaseOptions(model_asset_path=str(model_path))
            options = vision.FaceDetectorOptions(
                base_options=base_options,
                running_mode=vision.RunningMode.VIDEO,
                min_detection_confidence=min_detection_confidence,
            )
            detector = vision.FaceDetector.create_from_options(options)
        self._detector = detector
        self._bbox_margin_frac = bbox_margin_frac
        self._lock = face_lock or FaceLock()

    def process_item(self, ctx: FrameContext) -> FrameContext:
        t0 = time.perf_counter()
        ctx = self._detect(ctx)
        # Per-frame timings for the metrics log (`MetricsLogOutputStage`), in milliseconds.
        ctx.features.setdefault("timing_ms", {})["face"] = 1000.0 * (time.perf_counter() - t0)
        return ctx

    def _face_boxes(self, ctx: FrameContext) -> list[Box]:
        """Every detected face's raw box whose margin-expanded, frame-clipped box isn't empty."""
        import cv2
        import mediapipe as mp

        frame_rgb = cv2.cvtColor(ctx.frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        result = self._detector.detect_for_video(mp_image, ctx.timestamp_ms)
        frame_h, frame_w = ctx.frame_bgr.shape[:2]
        boxes = []
        for det in result.detections or []:
            b = det.bounding_box
            x0, y0, x1, y1 = _expand_and_clip_bbox(b.origin_x, b.origin_y, b.width, b.height,
                                                   frame_w, frame_h, self._bbox_margin_frac)
            if x1 > x0 and y1 > y0:
                boxes.append((b.origin_x, b.origin_y, b.width, b.height))
        return boxes

    def _detect(self, ctx: FrameContext) -> FrameContext:
        boxes = self._face_boxes(ctx)
        lock = self._lock.update(ctx.timestamp_ms / 1000.0, boxes)
        ctx.features["face_lock"] = {"faces": lock.faces, "ignored": lock.ignored,
                                     "switch_s": lock.switch_elapsed_s, "switched": lock.switched}
        if lock.switched:
            ctx.features["face_switched"] = True
        if lock.box is None:
            ctx.face_found = False
            return ctx

        x, y, w, h = lock.box
        frame_h, frame_w = ctx.frame_bgr.shape[:2]
        # Pixel-space box on the *original* frame (post-margin, pre-clip-to-crop) -- not needed
        # for inference, but lets a demo/overlay stage (see mjpeg_output_stage.py) draw it
        # without redoing detection itself.
        ctx.features["face_bbox"] = _expand_and_clip_bbox(x, y, w, h, frame_w, frame_h,
                                                          self._bbox_margin_frac)
        # The raw BlazeFace box (origin, size), before margin/clipping: the parts pipeline
        # rebuilds its square, black-padded training crop from this (model/parts_geometry.py).
        ctx.features["face_bbox_raw"] = lock.box
        ctx.face_found = True
        return ctx

    def close(self) -> None:
        self._detector.close()
