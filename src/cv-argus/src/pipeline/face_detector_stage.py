"""`FaceDetectorCropStage`: MediaPipe Face Detector (BlazeFace, short_range) -> the face box,
feeding `PartsInferenceStage`. Detection follows `notebook/06_dataset_creation_face_crops.ipynb`
and the parts dataset builder (`VIDEO` running mode, `min_detection_confidence`, the
highest-confidence face when more than one is found), because the parts detector was trained on
crops built from exactly this detector's boxes. It publishes the raw box (`face_bbox_raw`, which
`PartsInferenceStage` turns into the square training crop) and a margin-expanded box for the
overlay (`face_bbox`).
"""

import logging

import time

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from .. import constants
from .stage import FrameContext, Stage

logger = logging.getLogger(__name__)


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


class FaceDetectorCropStage(Stage):
    """Runs MediaPipe's Face Detector on each frame and records the best face's box. Frames with
    no confident detection are passed through with `face_found=False` rather than dropped —
    `PartsInferenceStage` (whose rules count "no face" time) and the outputs decide what that
    means; this stage only reports it.
    """

    def __init__(
        self,
        model_path,
        name: str = "face_detector_crop",
        min_detection_confidence: float = constants.FACE_DETECTOR_MIN_DETECTION_CONFIDENCE,
        bbox_margin_frac: float = constants.FACE_DETECTOR_BBOX_MARGIN_FRAC,
        **kwargs,
    ) -> None:
        super().__init__(name, **kwargs)
        base_options = mp_python.BaseOptions(model_asset_path=str(model_path))
        options = vision.FaceDetectorOptions(
            base_options=base_options,
            running_mode=vision.RunningMode.VIDEO,
            min_detection_confidence=min_detection_confidence,
        )
        self._detector = vision.FaceDetector.create_from_options(options)
        self._bbox_margin_frac = bbox_margin_frac

    def process_item(self, ctx: FrameContext) -> FrameContext:
        t0 = time.perf_counter()
        ctx = self._detect(ctx)
        # Per-frame timings for the metrics log (`MetricsLogOutputStage`), in milliseconds.
        ctx.features.setdefault("timing_ms", {})["face"] = 1000.0 * (time.perf_counter() - t0)
        return ctx

    def _detect(self, ctx: FrameContext) -> FrameContext:
        frame_bgr = ctx.frame_bgr
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        result = self._detector.detect_for_video(mp_image, ctx.timestamp_ms)

        if not result.detections:
            ctx.face_found = False
            return ctx

        # Highest-confidence detection when more than one face is found -- matches notebook 06;
        # num_faces isn't capped in FaceDetectorOptions itself, so this is where that's handled.
        best = max(result.detections, key=lambda d: d.categories[0].score)
        bbox = best.bounding_box
        frame_h, frame_w = frame_bgr.shape[:2]
        x0, y0, x1, y1 = _expand_and_clip_bbox(
            bbox.origin_x, bbox.origin_y, bbox.width, bbox.height,
            frame_w, frame_h, self._bbox_margin_frac,
        )
        if x1 <= x0 or y1 <= y0:
            ctx.face_found = False
            return ctx

        # Pixel-space box on the *original* frame (post-margin, pre-clip-to-crop) -- not needed
        # for inference, but lets a demo/overlay stage (see mjpeg_output_stage.py) draw it
        # without redoing detection itself.
        ctx.features["face_bbox"] = (x0, y0, x1, y1)
        # The raw BlazeFace box (origin, size), before margin/clipping: the parts pipeline
        # rebuilds its square, black-padded training crop from this (model/parts_geometry.py).
        ctx.features["face_bbox_raw"] = (bbox.origin_x, bbox.origin_y, bbox.width, bbox.height)
        ctx.face_found = True
        return ctx

    def close(self) -> None:
        self._detector.close()
