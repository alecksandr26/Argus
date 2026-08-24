"""Stages that turn a `FrameContext`'s MediaPipe output into a drowsiness prediction, by
wrapping the `model/` detectors. Both stages are thin on purpose: they just call the detector
and stash the result on `ctx.detection` — all the actual model logic lives in `model/`, per the
`model/`-vs-`pipeline/` boundary documented in `model/detector.py` and `model/cnn_detector.py`
(neither has a `mediapipe` import; translating MediaPipe output into what they expect is
`pipeline/`'s job, done by `FaceDetectorCropStage`/`FaceLandmarkerStage` upstream of these).
"""

import logging

from ..model import CnnDrowsinessDetector, DrowsinessDetector
from .stage import FrameContext, Stage

logger = logging.getLogger(__name__)


class CnnInferenceStage(Stage):
    """Wraps `CnnDrowsinessDetector` — the model this container actually deploys (see
    `src/cv-argus/CLAUDE.md`'s "Current status"). Stateless per frame: unlike the LSTM path,
    there's no buffer to keep in order, so a dropped or out-of-order frame here just means one
    missed prediction, not corrupted state.

    `predict_crop()` expects an already-RGB, not-yet-resized crop (see
    `FaceDetectorCropStage.process_item()` for the `BGR->RGB` conversion) and does its own
    resize + rescale internally — this stage doesn't need to know `CNN_IMG_SIZE` at all.
    """

    def __init__(self, detector: CnnDrowsinessDetector, name: str = "cnn_inference", **kwargs) -> None:
        super().__init__(name, **kwargs)
        self._detector = detector

    def process_item(self, ctx: FrameContext) -> FrameContext:
        if not ctx.face_found or "face_crop_rgb" not in ctx.features:
            return ctx
        ctx.detection = self._detector.predict_crop(ctx.features["face_crop_rgb"])
        return ctx


class LstmInferenceStage(Stage):
    """Wraps the existing `DrowsinessDetector` (LSTM + geometric features, kept, optional — see
    `src/cv-argus/CLAUDE.md`'s "Current status"). Must be called once per incoming frame, in
    order: `DrowsinessDetector`'s internal `feature_buffer` is what makes it stateful, not this
    stage, so skipping frames or reordering them would corrupt its sliding window. Frames with
    no detected face are passed through untouched (not fed to the detector), matching
    `predict_frame()`'s expectation of real per-frame landmark data.
    """

    def __init__(self, detector: DrowsinessDetector, name: str = "lstm_inference", **kwargs) -> None:
        super().__init__(name, **kwargs)
        self._detector = detector

    def process_item(self, ctx: FrameContext) -> FrameContext:
        if not ctx.face_found:
            return ctx
        ctx.detection = self._detector.predict_frame(
            ctx.features["landmarks_xy"],
            ctx.features["rotation_matrix"],
            ctx.features["blendshape_scores"],
        )
        return ctx
