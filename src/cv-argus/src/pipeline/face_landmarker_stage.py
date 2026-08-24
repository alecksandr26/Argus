"""`FaceLandmarkerStage`: MediaPipe FaceLandmarker -> raw landmarks/pose/blendshapes for the
(now-optional) LSTM path, feeding `LstmInferenceStage`. This is the camera-loop piece
`src/cv-argus/CLAUDE.md` flagged as "still unbuilt" for `pipeline/` — follows the notebook's
"End-to-End Live Stream Simulation" pattern exactly: `VIDEO` running mode,
`detect_for_video(mp_image, timestamp_ms)` with a monotonically increasing wall-clock
timestamp, not MediaPipe's `LIVE_STREAM` callback API (a different shape never validated in the
notebook).

Translating a `FaceLandmarkerResult` into the plain `landmarks_xy`/`rotation_matrix`/
`blendshape_scores` arguments `DrowsinessDetector.predict_frame()` expects is deliberately
`pipeline/`'s job, not `model/`'s — see `model/detector.py`'s module docstring for why (`model/`
has no `mediapipe` import at all, by design).
"""

import logging

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from .. import constants
from .stage import FrameContext, Stage

logger = logging.getLogger(__name__)


class FaceLandmarkerStage(Stage):
    """Runs MediaPipe's FaceLandmarker on each frame and extracts the three raw fields the LSTM
    model needs. Frames with no detected face pass through with `face_found=False`.
    """

    def __init__(
        self,
        model_path,
        name: str = "face_landmarker",
        min_confidence: float = constants.FACE_LANDMARKER_MIN_CONFIDENCE,
        **kwargs,
    ) -> None:
        super().__init__(name, **kwargs)
        base_options = mp_python.BaseOptions(model_asset_path=str(model_path))
        options = vision.FaceLandmarkerOptions(
            base_options=base_options,
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=min_confidence,
            min_face_presence_confidence=min_confidence,
            min_tracking_confidence=min_confidence,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)

    def process_item(self, ctx: FrameContext) -> FrameContext:
        frame_rgb = cv2.cvtColor(ctx.frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        result = self._landmarker.detect_for_video(mp_image, ctx.timestamp_ms)

        if not result.face_landmarks:
            ctx.face_found = False
            return ctx

        landmarks_xy = np.array(
            [[lm.x, lm.y] for lm in result.face_landmarks[0]], dtype=np.float32
        )
        # FaceLandmarker doesn't hand back a bounding box directly (unlike FaceDetector) -- but
        # one's cheap to derive from the landmark point cloud's extent, scaled from normalized
        # [0, 1] coordinates to frame pixels. Not used by predict_frame() at all; this is purely
        # so a demo/overlay stage (see mjpeg_output_stage.py) can draw a box for this pipeline
        # too, the same as the CNN path's FaceDetectorCropStage already can.
        frame_h, frame_w = ctx.frame_bgr.shape[:2]
        xs, ys = landmarks_xy[:, 0] * frame_w, landmarks_xy[:, 1] * frame_h
        ctx.features["face_bbox"] = (
            int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
        )
        rotation_matrix = (
            np.array(result.facial_transformation_matrixes[0], dtype=np.float32)[:3, :3]
            if result.facial_transformation_matrixes
            # Identity when pose isn't available -- matches DrowsinessDetector.predict_frame()'s
            # own documented convention for this case (see model/detector.py).
            else np.eye(3, dtype=np.float32)
        )
        blendshape_scores = (
            {b.category_name: b.score for b in result.face_blendshapes[0]}
            if result.face_blendshapes
            else {}
        )

        ctx.features["landmarks_xy"] = landmarks_xy
        ctx.features["rotation_matrix"] = rotation_matrix
        ctx.features["blendshape_scores"] = blendshape_scores
        ctx.face_found = True
        return ctx

    def close(self) -> None:
        self._landmarker.close()
