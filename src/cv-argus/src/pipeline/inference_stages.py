"""Stage that turns a `FrameContext`'s MediaPipe output into a drowsiness prediction, by
wrapping `model/`'s `FusedDrowsinessDetector`. Thin on purpose: it just calls the detector and
stashes the result on `ctx.detection` — all the actual model logic lives in `model/`, per the
`model/`-vs-`pipeline/` boundary documented in `model/fused_detector.py` (no `mediapipe` import
there; translating MediaPipe output into what it expects is done by `FaceDetectorCropStage`/
`FaceLandmarkerCropStage` upstream of this stage).
"""

import logging

from ..model import FusedDrowsinessDetector
from .stage import FrameContext, Stage

logger = logging.getLogger(__name__)


class FusedInferenceStage(Stage):
    """Wraps `FusedDrowsinessDetector` — the model this container deploys (the frozen-CNN-
    embedding + geometric-feature + LSTM classifier). Stateful and order-sensitive (own window
    buffer, must be called in order per camera stream), but a frame with no face crop at all
    skips the tick entirely rather than being fed to the detector — the project's chosen
    behavior for a totally-missed BlazeFace detection, matching how `06`'s extraction only ever
    sampled frames where a crop was actually found (see `face_landmarker_crop_stage.py`'s module
    docstring for the full reasoning). A frame *with* a crop but no landmarker detection still
    reaches the detector, with an all-zero geo-feature vector already set by
    `FaceLandmarkerCropStage`.
    """

    def __init__(self, detector: FusedDrowsinessDetector, name: str = "fused_inference", **kwargs) -> None:
        super().__init__(name, **kwargs)
        self._detector = detector

    def process_item(self, ctx: FrameContext) -> FrameContext:
        if not ctx.face_found or "face_crop_rgb" not in ctx.features:
            return ctx
        fused_geo = ctx.features.get("fused_geo_features")
        if fused_geo is None:
            # FaceLandmarkerCropStage hasn't run on this ctx -- shouldn't happen given main.py's
            # wiring (it always sits between FaceDetectorCropStage and this stage), but don't
            # crash a live pipeline over a wiring bug elsewhere.
            logger.warning(
                "%s: face_crop_rgb present but fused_geo_features missing -- "
                "is FaceLandmarkerCropStage wired upstream of this stage?", self.name
            )
            return ctx
        ctx.detection = self._detector.predict_frame(ctx.features["face_crop_rgb"], fused_geo)
        return ctx
