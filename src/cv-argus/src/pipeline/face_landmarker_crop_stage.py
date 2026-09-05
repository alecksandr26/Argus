"""`FaceLandmarkerCropStage`: MediaPipe FaceLandmarker, **`IMAGE` running mode**, run on
`ctx.features["face_crop_rgb"]` (the crop `FaceDetectorCropStage` produced) — not the full
frame, and not `VIDEO` mode. Feeds `FusedInferenceStage`'s 10-dim geometric-feature subset
(`model/fused_features.py`).

**Why `IMAGE` mode on the crop, not `VIDEO` mode on the full frame — this is a training-fidelity
requirement, not a style choice.** `notebook/09_dataset_creation_cnn_lstm.ipynb`'s geometric-
feature extraction ran FaceLandmarker on `notebook/06_dataset_creation_face_crops.ipynb`'s
*saved crop image* (`cv2.imread` of the crop file), independently per crop, in `IMAGE` mode — not
on the original video frame, and not in `VIDEO` mode. This isn't just a notebook idiosyncrasy:
`src/dataset/argus_dataset/pipelines.py`'s `extract_geo_for_crop()` is the actual non-notebook
code that produced the real training CSVs, and it does exactly this too. So this stage must run
*downstream of* `FaceDetectorCropStage` and consume its crop, not derive its own bbox by running
FaceLandmarker on the full frame instead — that would be a different, unvalidated
feature-extraction path.

A frame with a crop but no landmark detection is **not dropped**: `ctx.features
["fused_geo_features"]` becomes an all-zero 10-vector and the frame still reaches
`FusedInferenceStage`, matching `09`'s/`extract_geo_for_crop`'s own no-detection fallback (a
crop that failed FaceLandmarker still contributed a real, zero-geo frame to a training window).
A frame with **no crop at all** (`FaceDetectorCropStage` found nothing) is left alone here —
`FusedInferenceStage` is what decides that means skipping the tick entirely, per this project's
design decision (closest match to how `06`'s extraction only ever sampled frames where BlazeFace
succeeded in the first place).
"""

import logging

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from .. import constants
from ..model.fused_features import compute_fused_geo_features, zero_fused_geo_features
from ..model.layers import GeometricRatioFeatureLayer
from .stage import FrameContext, Stage

logger = logging.getLogger(__name__)


class FaceLandmarkerCropStage(Stage):
    """Runs MediaPipe's FaceLandmarker, in `IMAGE` mode, on `ctx.features["face_crop_rgb"]` and
    sets `ctx.features["fused_geo_features"]` — see this module's docstring for why `IMAGE` mode
    on the crop, not `VIDEO` mode on the full frame.
    """

    def __init__(
        self,
        model_path,
        name: str = "face_landmarker_crop",
        min_confidence: float = constants.FACE_LANDMARKER_MIN_CONFIDENCE,
        **kwargs,
    ) -> None:
        super().__init__(name, **kwargs)
        base_options = mp_python.BaseOptions(model_asset_path=str(model_path))
        options = vision.FaceLandmarkerOptions(
            base_options=base_options,
            running_mode=vision.RunningMode.IMAGE,
            num_faces=1,
            min_face_detection_confidence=min_confidence,
            min_face_presence_confidence=min_confidence,
            min_tracking_confidence=min_confidence,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)
        # One instance, reused across every frame -- see fused_features.compute_fused_geo_features's
        # `ratio_layer` argument.
        self._ratio_layer = GeometricRatioFeatureLayer()

    def process_item(self, ctx: FrameContext) -> FrameContext:
        if not ctx.face_found or "face_crop_rgb" not in ctx.features:
            # No crop this frame (FaceDetectorCropStage found nothing) -- nothing to landmark.
            # FusedInferenceStage is what turns this into "skip the tick".
            return ctx

        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=ctx.features["face_crop_rgb"])
        result = self._landmarker.detect(mp_image)  # IMAGE mode: no timestamp argument

        if not result.face_landmarks:
            ctx.features["fused_geo_features"] = zero_fused_geo_features()
            return ctx

        landmarks_xy = np.array(
            [[lm.x, lm.y] for lm in result.face_landmarks[0]], dtype=np.float32
        )
        rotation_matrix = (
            np.array(result.facial_transformation_matrixes[0], dtype=np.float32)[:3, :3]
            if result.facial_transformation_matrixes
            else np.eye(3, dtype=np.float32)
        )
        blendshape_scores = (
            {b.category_name: b.score for b in result.face_blendshapes[0]}
            if result.face_blendshapes
            else {}
        )
        ctx.features["fused_geo_features"] = compute_fused_geo_features(
            landmarks_xy, rotation_matrix, blendshape_scores, ratio_layer=self._ratio_layer
        )
        return ctx

    def close(self) -> None:
        self._landmarker.close()
