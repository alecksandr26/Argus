"""`FaceDetectorCropStage`: MediaPipe Face Detector (BlazeFace, short_range) -> a cropped face
image, feeding `FaceLandmarkerCropStage` and ultimately `FusedInferenceStage`. Ports
`notebook/06_dataset_creation_face_crops.ipynb`'s `FaceCropExtractionPipeline` extraction logic
verbatim (`VIDEO` running mode, `min_detection_confidence`, `bbox_margin_frac`, highest-
confidence-detection selection when more than one face is found) because the deployed model's
frozen CNN embedding backbone was trained on crops built exactly this way — diverging here is a
silent train/inference skew, not something that would show up as an exception.
"""

import logging

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
    """Runs MediaPipe's Face Detector on each frame and, when a face is found, crops it (with
    margin) and converts it to RGB for the frozen CNN embedder. Frames with no confident
    detection are passed through with `face_found=False` rather than dropped —
    `FaceLandmarkerCropStage`/`FusedInferenceStage` and the output stage decide what "no
    detection this frame" means; this stage only reports it.
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

        crop_bgr = frame_bgr[y0:y1, x0:x1]
        # BGR -> RGB explicitly: notebook 06 wrote crops via cv2.imwrite(BGR array), which
        # encodes a standard RGB-ordered JPEG; 07_cnn_training.ipynb then read them back with
        # tf.io.decode_jpeg (RGB). The CNN was trained on RGB pixels. There's no JPEG file here
        # to do that conversion for us implicitly, so it has to happen explicitly -- skipping
        # this doesn't error, it silently feeds a channel-swapped image and degrades accuracy.
        ctx.features["face_crop_rgb"] = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        # Pixel-space box on the *original* frame (post-margin, pre-clip-to-crop) -- not needed
        # for inference, but lets a demo/overlay stage (see mjpeg_output_stage.py) draw it
        # without redoing detection itself.
        ctx.features["face_bbox"] = (x0, y0, x1, y1)
        ctx.face_found = True
        return ctx

    def close(self) -> None:
        self._detector.close()
