"""`FaceDetectorCropStage`: MediaPipe Face Detector (BlazeFace, short_range) -> the driver's face
box, feeding `PartsInferenceStage`. Detection follows `notebook/06_dataset_creation_face_crops.ipynb`
and the parts dataset builder (`VIDEO` running mode, `min_detection_confidence`), because the
parts detector was trained on crops built from exactly this detector's boxes. It publishes the raw
box (`face_bbox_raw`, which `PartsInferenceStage` turns into the square training crop) and a
margin-expanded box for the overlay (`face_bbox`).

**One face** (`FaceLock`, contracts C1.7 `[sync 2]`). A cab can hold a passenger, or someone
leaning in at a stop, and only the driver counts:
- With no lock, the **largest** face is locked (the one nearest the camera: the driver).
- **Matching.** A detection matches a tracked box (the locked face or a bystander) when its
  centre is within `FACE_LOCK_MAX_SHIFT_FRAC` x the tracked box's side and its side is within a
  factor `FACE_LOCK_MAX_SCALE` of it; the tracked box then follows the detection.
- **Bystanders** are the other faces seen while the locked face is matched. They are carried
  forward by matching and dropped after `FACE_LOCK_MISS_GRACE_S` unseen, so a one-frame detector
  miss doesn't turn the passenger into a "new" face.
- **While the locked face is missing:**
  1. a *new* face (one that matches no bystander: the driver's box jumped, or came back
     displaced after a gap) re-acquires the lock on the same frame (`face_found = True`, no
     switch, no rule reset, switch timer reset);
  2. only bystanders visible -> `face_found = False` and the **switch timer** runs. On the
     switch frame (timer `>= FACE_SWITCH_AFTER_S`, on the frame timestamps the rule engine uses)
     the frame still reads no face and is flagged `ctx.features["face_switched"] = True`; the
     lock moves to the largest visible face, used from the next frame;
  3. nothing visible -> `face_found = False`; the switch timer resets, unless a bystander is
     still inside its miss grace (a one-frame passenger miss doesn't restart the 5 s).
- **Take-over.** Another face with a side `>= FACE_LOCK_TAKEOVER_SCALE` x the locked face's,
  visible for `FACE_SWITCH_AFTER_S` while the locked face is visible too (gaps up to the miss
  grace allowed), takes the lock: the driver, nearest the camera, takes it back from a passenger.
  The take-over frame still reads the old locked face and is flagged `face_switched`; the new face
  is used from the next frame. A smaller second face is ignored.
- After either switch `PartsInferenceStage` feeds the flagged frame to the rules first, then
  resets the rule timers and the tracker (never the held severity).
- Known limit (accepted): a face never seen next to the driver can't be told apart from the
  driver coming back, so it is re-acquired at once (rule 1).
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
    switched: bool = False           # a switch or take-over frame (the lock moves after it)
    switch_elapsed_s: float | None = None     # missing-face switch timer, while it runs
    takeover_elapsed_s: float | None = None   # take-over timer, while it runs
    reacquired: bool = False         # the locked face was missing and a new face took the lock

    @property
    def ignored(self) -> int:
        return self.faces - (1 if self.box is not None else 0)


def _side(box: Box) -> float:
    return float(max(box[2], box[3], 1))


def _centre(box: Box) -> tuple[float, float]:
    return box[0] + box[2] / 2.0, box[1] + box[3] / 2.0


@dataclass
class _Tracked:
    """A bystander or the take-over candidate: its last matched box and when it was seen."""
    box: Box
    last_seen: float


class FaceLock:
    """Pure (no MediaPipe) face lock described in the module docstring. `update(t, boxes)` takes
    the frame time in seconds and every face box detected on that frame."""

    def __init__(self, switch_after_s: float = constants.FACE_SWITCH_AFTER_S,
                 max_shift_frac: float = constants.FACE_LOCK_MAX_SHIFT_FRAC,
                 max_scale: float = constants.FACE_LOCK_MAX_SCALE,
                 miss_grace_s: float = constants.FACE_LOCK_MISS_GRACE_S,
                 takeover_scale: float = constants.FACE_LOCK_TAKEOVER_SCALE) -> None:
        self._switch_after = switch_after_s
        self._max_shift = max_shift_frac
        self._max_scale = max_scale
        self._grace = miss_grace_s
        self._takeover_scale = takeover_scale
        self.reset()

    @property
    def locked_box(self) -> Box | None:
        return self._box

    @property
    def bystanders(self) -> list[Box]:
        return [b.box for b in self._bystanders]

    def reset(self) -> None:
        self._box: Box | None = None
        self._bystanders: list[_Tracked] = []
        self._switch_since: float | None = None
        self._candidate: _Tracked | None = None      # take-over candidate
        self._candidate_since: float | None = None

    # --- matching ---------------------------------------------------------------------------

    def _distance(self, tracked: Box, det: Box) -> float | None:
        """Centre distance when `det` matches `tracked` (shift and scale limits), else `None`."""
        side = _side(tracked)
        scale = _side(det) / side
        if not (1 / self._max_scale <= scale <= self._max_scale):
            return None
        (tx, ty), (dx, dy) = _centre(tracked), _centre(det)
        d = ((dx - tx) ** 2 + (dy - ty) ** 2) ** 0.5
        return d if d <= self._max_shift * side else None

    def _best(self, tracked: Box, boxes: list[Box], taken: set[int]) -> int | None:
        """Index of the free detection matching `tracked` with the closest centre, or `None`."""
        best, best_d = None, None
        for k, det in enumerate(boxes):
            if k in taken:
                continue
            d = self._distance(tracked, det)
            if d is not None and (best_d is None or d < best_d):
                best, best_d = k, d
        return best

    def _carry_bystanders(self, t: float, boxes: list[Box], taken: set[int]) -> set[int]:
        """Match each bystander to its closest free detection (the bystander follows it); drop
        the ones unseen longer than the grace. Returns the indices of the detections taken."""
        used: set[int] = set()
        kept = []
        for b in self._bystanders:
            k = self._best(b.box, boxes, taken | used)
            if k is not None:
                used.add(k)
                b.box, b.last_seen = boxes[k], t
            if t - b.last_seen <= self._grace:
                kept.append(b)
        self._bystanders = kept
        return used

    def _carry_candidate(self, t: float, boxes: list[Box], taken: set[int]) -> None:
        """Follow the take-over candidate by matching; drop it after the grace unseen."""
        if self._candidate is None:
            return
        k = self._best(self._candidate.box, boxes, taken)
        if k is not None:
            self._candidate.box, self._candidate.last_seen = boxes[k], t
        elif t - self._candidate.last_seen > self._grace:
            self._drop_candidate()

    def _drop_candidate(self) -> None:
        self._candidate, self._candidate_since = None, None

    def _lock_on(self, t: float, boxes: list[Box], k: int) -> None:
        """A fresh lock on `boxes[k]`; the frame's other faces become the bystanders."""
        self._box = boxes[k]
        self._bystanders = [_Tracked(b, t) for i, b in enumerate(boxes) if i != k]
        self._switch_since = None
        self._drop_candidate()

    # --- per frame --------------------------------------------------------------------------

    def update(self, t: float, boxes: list[Box]) -> FaceLockResult:
        boxes = list(boxes)
        if self._box is None:
            if not boxes:
                return FaceLockResult(None, 0)
            k = max(range(len(boxes)), key=lambda i: _area(boxes[i]))
            self._lock_on(t, boxes, k)
            return FaceLockResult(self._box, len(boxes))

        k = self._best(self._box, boxes, set())
        if k is not None:
            return self._locked_visible(t, boxes, k)
        return self._locked_missing(t, boxes)

    def _locked_visible(self, t: float, boxes: list[Box], k: int) -> FaceLockResult:
        locked = boxes[k]
        self._box, self._switch_since = locked, None
        # Every other face is a bystander. One the detector missed on this frame stays for the
        # grace, so a single miss right before the driver leaves doesn't make it a "new" face.
        used = self._carry_bystanders(t, boxes, {k})
        self._bystanders += [_Tracked(b, t) for i, b in enumerate(boxes) if i != k and i not in used]

        # Take-over: a clearly larger face, continuously in view (gaps up to the grace).
        need = self._takeover_scale * _side(locked)
        self._carry_candidate(t, boxes, {k})
        cand = self._candidate
        if cand is not None and cand.last_seen == t and _side(cand.box) < need:
            self._drop_candidate()                     # in view but no longer large enough
        if self._candidate is None:
            big = [i for i, b in enumerate(boxes) if i != k and _side(b) >= need]
            if big:
                i = max(big, key=lambda j: _area(boxes[j]))
                self._candidate, self._candidate_since = _Tracked(boxes[i], t), t
        if self._candidate is None:
            return FaceLockResult(locked, len(boxes))

        elapsed = t - self._candidate_since
        if elapsed >= self._switch_after and self._candidate.last_seen == t:
            new_box = self._candidate.box
            logger.info("face lock: a face %.2fx larger stayed in view %.1f s; the lock moves to %s",
                        _side(new_box) / _side(locked), elapsed, new_box)
            self._lock_on(t, boxes, boxes.index(new_box))
            return FaceLockResult(locked, len(boxes), switched=True, takeover_elapsed_s=elapsed)
        return FaceLockResult(locked, len(boxes), takeover_elapsed_s=elapsed)

    def _locked_missing(self, t: float, boxes: list[Box]) -> FaceLockResult:
        # The take-over candidate is kept through short gaps; its timer is only checked while
        # the locked face is visible.
        self._carry_candidate(t, boxes, set())
        used = self._carry_bystanders(t, boxes, set())
        # Rule 1: a detection that matches no bystander at all is a new face.
        new = [i for i in range(len(boxes)) if i not in used
               and all(self._distance(b.box, boxes[i]) is None for b in self._bystanders)]
        if new:
            i = max(new, key=lambda j: _area(boxes[j]))
            logger.debug("face lock: locked face missing; re-acquired a new face %s", boxes[i])
            self._box, self._switch_since = boxes[i], None
            self._drop_candidate()
            # The other new faces join the bystanders (the lock matched on this frame).
            self._bystanders += [_Tracked(boxes[j], t) for j in new if j != i]
            return FaceLockResult(boxes[i], len(boxes), reacquired=True)

        if not boxes:
            # Rule 3. A bystander still inside its miss grace keeps the switch timer running (a
            # one-frame passenger miss doesn't restart the 5 s); the switch itself waits for a
            # frame with a face to move to.
            if not self._bystanders:
                self._switch_since = None
            elapsed = None if self._switch_since is None else t - self._switch_since
            return FaceLockResult(None, 0, switch_elapsed_s=elapsed)

        # Rule 2: only bystanders in view.
        if self._switch_since is None:
            self._switch_since = t
        elapsed = t - self._switch_since
        if elapsed >= self._switch_after:
            k = max(range(len(boxes)), key=lambda i: _area(boxes[i]))
            logger.info("face lock: locked face gone %.1f s with another face in view; switching to "
                        "the largest face %s", elapsed, boxes[k])
            self._lock_on(t, boxes, k)
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
                                     "switch_s": lock.switch_elapsed_s,
                                     "takeover_s": lock.takeover_elapsed_s,
                                     "switched": lock.switched}
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
