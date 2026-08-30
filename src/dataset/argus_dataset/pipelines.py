"""MediaPipe extraction pipelines — verbatim ports of the notebook classes.

  * ``FaceLandmarkerFeatureExtractor``  — port of src/notebook/01 cell 85 / 02 cell 73.
        FaceLandmarker (VIDEO mode), one clip -> ``(np.ndarray[n_frames, 58] float32, dropped)``.
  * ``FaceCropExtractor``               — port of src/notebook/06 cell 9.
        BlazeFace FaceDetector (VIDEO mode), one clip -> ``[(frame_idx, sample_idx, crop_bgr), ...]``.
  * ``extract_geo_for_crop``            — port of src/notebook/09 cell 13.
        FaceLandmarker (IMAGE mode) on one saved crop -> the 10 ``GEO_FEATURE_NAMES`` values.

``cv2`` / ``mediapipe`` are imported lazily inside the methods so this module can be imported
in the parent process (to ship the pipeline objects to workers) without loading native
libraries before ``workers.pin_worker_threads()`` has run in the worker.

Each pipeline instance holds only picklable primitives (a model-path string + numbers), never a
live MediaPipe handle — matching src/notebook/06's design and avoiding the segfault-on-unpickle
that src/notebook/01's comments describe.
"""

from __future__ import annotations

import os

import numpy as np

from . import config, geometry


# --- MediaPipe option/handle factories (lazy) ----------------------------------------------

def _make_landmarker(model_path: str, running_mode: str):
    import mediapipe as mp  # noqa: F401  (kept for symmetry / future use)
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    modes = {"VIDEO": vision.RunningMode.VIDEO, "IMAGE": vision.RunningMode.IMAGE}
    options = vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=os.path.abspath(model_path)),
        running_mode=modes[running_mode],
        num_faces=1,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
        min_tracking_confidence=0.5,
        output_face_blendshapes=True,
        output_facial_transformation_matrixes=True,
    )
    return vision.FaceLandmarker.create_from_options(options)


def _make_detector(model_path: str, running_mode: str, min_detection_confidence: float):
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    modes = {"VIDEO": vision.RunningMode.VIDEO, "IMAGE": vision.RunningMode.IMAGE}
    options = vision.FaceDetectorOptions(
        base_options=mp_python.BaseOptions(model_asset_path=os.path.abspath(model_path)),
        running_mode=modes[running_mode],
        min_detection_confidence=min_detection_confidence,
    )
    return vision.FaceDetector.create_from_options(options)


def _landmarks_and_matrix(result):
    """(478,2) landmark array + (3,3) rotation matrix from a FaceLandmarker result, or None."""
    if not result.face_landmarks:
        return None
    lm = result.face_landmarks[0]
    landmarks_xy = np.array([[p.x, p.y] for p in lm], dtype=np.float64)
    if result.facial_transformation_matrixes:
        R = np.array(result.facial_transformation_matrixes[0])[:3, :3]
    else:
        R = np.eye(3, dtype=np.float64)
    return landmarks_xy, R


def _blendshape_dict(result) -> dict[str, float]:
    if not result.face_blendshapes:
        return {}
    return {b.category_name: b.score for b in result.face_blendshapes[0]}


# --- 1. windowed / flat geometric features (FaceLandmarker, VIDEO mode) --------------------

class FaceLandmarkerFeatureExtractor:
    """Port of src/notebook/01 cell 85 (``FeatureExtractionPipeline``). Same ``frame_stride``,
    ``timestamp_ms``, all-zero dummy row on a missed frame, and fresh-detector-per-video."""

    def __init__(self, model_path: str, sampling_fps: int = config.SAMPLING_FPS):
        self.model_path = str(model_path)
        self.sampling_fps = sampling_fps

    def process_video(self, video_path: str):
        import cv2
        import mediapipe as mp

        detector = _make_landmarker(self.model_path, "VIDEO")
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            detector.close()
            raise IOError(f"Cannot open video: {video_path}")

        src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_stride = max(1, round(src_fps / self.sampling_fps))

        rows: list[np.ndarray] = []
        dropped = 0
        frame_idx = 0
        try:
            while True:
                ok, frame_bgr = cap.read()
                if not ok:
                    break
                if frame_idx % frame_stride == 0:
                    frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
                    timestamp_ms = int(frame_idx * (1000.0 / src_fps))
                    result = detector.detect_for_video(mp_image, timestamp_ms)

                    lm = _landmarks_and_matrix(result)
                    if lm is not None:
                        landmarks_xy, R = lm
                        rows.append(
                            geometry.frame_feature_row(landmarks_xy, R, _blendshape_dict(result))
                        )
                    else:
                        dropped += 1
                        rows.append(np.zeros(config.NUM_FEATURES, dtype=np.float32))
                frame_idx += 1
        finally:
            cap.release()
            detector.close()

        if not rows:
            return None, 0
        return np.asarray(rows, dtype=np.float32), dropped


# --- 2. face crops (BlazeFace FaceDetector, VIDEO mode) -----------------------------------

class FaceCropExtractor:
    """Port of src/notebook/06 cell 9 (``FaceCropExtractionPipeline``)."""

    def __init__(
        self,
        model_path: str,
        min_detection_confidence: float = config.CROP_MIN_DETECTION_CONFIDENCE,
        bbox_margin_frac: float = config.CROP_BBOX_MARGIN_FRAC,
        sampling_fps: int = config.SAMPLING_FPS,
        max_frames_per_clip: int | None = config.CROP_MAX_FRAMES_PER_CLIP,
    ):
        self.model_path = str(model_path)
        self.min_detection_confidence = min_detection_confidence
        self.bbox_margin_frac = bbox_margin_frac
        self.sampling_fps = sampling_fps
        self.max_frames_per_clip = max_frames_per_clip

    def _expand_and_clip_bbox(self, x, y, w, h, frame_w, frame_h):
        mx, my = int(w * self.bbox_margin_frac), int(h * self.bbox_margin_frac)
        return (max(0, x - mx), max(0, y - my),
                min(frame_w, x + w + mx), min(frame_h, y + h + my))

    def process_video(self, video_path: str):
        import cv2
        import mediapipe as mp

        detector = _make_detector(self.model_path, "VIDEO", self.min_detection_confidence)
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            detector.close()
            raise IOError(f"Cannot open video: {video_path}")

        src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_stride = max(1, round(src_fps / self.sampling_fps))

        crops: list[tuple[int, int, np.ndarray]] = []
        frame_idx = 0
        sample_idx = 0  # once per SAMPLED frame, regardless of detection — 09 needs the gaps
        try:
            while True:
                ok, frame_bgr = cap.read()
                if not ok:
                    break
                if frame_idx % frame_stride == 0:
                    frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
                    timestamp_ms = int(frame_idx * (1000.0 / src_fps))
                    result = detector.detect_for_video(mp_image, timestamp_ms)

                    if result.detections:
                        best = max(result.detections, key=lambda d: d.categories[0].score)
                        bb = best.bounding_box
                        fh, fw = frame_bgr.shape[:2]
                        x0, y0, x1, y1 = self._expand_and_clip_bbox(
                            bb.origin_x, bb.origin_y, bb.width, bb.height, fw, fh
                        )
                        if x1 > x0 and y1 > y0:
                            crops.append((frame_idx, sample_idx, frame_bgr[y0:y1, x0:x1].copy()))
                    sample_idx += 1
                    if (self.max_frames_per_clip is not None
                            and len(crops) >= self.max_frames_per_clip):
                        break
                frame_idx += 1
        finally:
            cap.release()
            detector.close()
        return crops


# --- 3. per-crop geometry for the CNN+LSTM fusion input (FaceLandmarker, IMAGE mode) ------

def extract_geo_for_crop(image_path: str, landmarker) -> list[float]:
    """Port of src/notebook/09 cell 13 ``_extract_geo_features``. ``landmarker`` is an
    IMAGE-mode ``FaceLandmarker`` (reused across crops — no timestamp state). Returns the 10
    ``config.GEO_FEATURE_NAMES`` values; all-zeros if the crop can't be read or no face is found.
    """
    import cv2
    import mediapipe as mp

    zeros = [0.0] * config.NUM_GEO_FEATURES
    bgr = cv2.imread(image_path)
    if bgr is None:
        return zeros
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    result = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))

    lm = _landmarks_and_matrix(result)
    if lm is None:
        return zeros
    landmarks_xy, R = lm

    geo = geometry.compute_geometric_features(landmarks_xy, R)
    base = dict(zip(config.GEOMETRIC_FEATURE_NAMES, geo.tolist()))
    all_feats = {**base, **_blendshape_dict(result)}
    return [float(all_feats.get(name, 0.0)) for name in config.GEO_FEATURE_NAMES]


def make_image_landmarker(model_path: str):
    """An IMAGE-mode FaceLandmarker for :func:`extract_geo_for_crop`. Caller closes it."""
    return _make_landmarker(str(model_path), "IMAGE")
