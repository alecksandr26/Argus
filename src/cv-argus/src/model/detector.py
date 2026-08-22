"""Loads the trained end-to-end model and wraps it in a per-frame `predict_frame()` call.

This is the thing `pipeline/` (once it exists) will actually hold onto: one
`DrowsinessDetector` per camera stream, fed one frame's raw landmarks/pose/blendshapes at a
time. Deliberately has **no import of `mediapipe` anywhere in this module** — `predict_frame`
takes plain `numpy` arrays and a `dict`, not a MediaPipe `FaceLandmarkerResult`. That's a
boundary, not an oversight: `pipeline/` is the module that owns the MediaPipe `FaceLandmarker`
object and the camera loop (see `notebook/03_deployment_export.ipynb`'s "End-to-End Live
Stream Simulation" cell for the `VIDEO` running mode / `detect_for_video(mp_image,
timestamp_ms)` pattern to replicate there),
and it's `pipeline/`'s job to pull `.face_landmarks[0]`, `.facial_transformation_matrixes[0]`,
`.face_blendshapes[0]` out of the result and call `predict_frame(landmarks_xy, rotation_matrix,
blendshape_scores)` with them. Keeping that translation out of `model/` means this module can
be unit-tested with synthetic arrays alone (no camera, no MediaPipe task graph running), and
`pipeline/`'s camera backend (`cv2.VideoCapture` on a dev laptop vs. `picamera2` on the Pi) can
change without touching this file at all.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tensorflow as tf

from .downloader import download_model
from .layers import GeometricRatioFeatureLayer
from .lstm_model import LstmGeometricFeatureModel

logger = logging.getLogger(__name__)

# 0-indexed softmax output -> the notebook's 1-3 drowsiness class (1 = Alert, 3 = Drowsy).
# See the README's "Output" note under model/'s must-match-the-notebook section.
_LEVEL_OFFSET = 1
_CLASS_NAMES = {1: "Alert", 2: "Low Vigilant", 3: "Drowsy"}


@dataclass
class DetectionResult:
    """One frame's prediction. `level` is already offset back to the notebook's 1-3 scale."""

    level: int
    probabilities: np.ndarray  # shape (3,), softmax over classes 1-3 (Alert/Low Vigilant/Drowsy) in order

    @property
    def class_name(self) -> str:
        """Human-readable label for `level` (\"Alert\" / \"Low Vigilant\" / \"Drowsy\")."""
        return _CLASS_NAMES.get(self.level, f"Unknown({self.level})")


class DrowsinessDetector:
    """Loads `LstmGeometricFeatureModel` and turns raw per-frame MediaPipe output into a
    drowsiness level.

    How the "state" actually works (worth knowing before trying to optimize this): the LSTM
    layers inside `lstm_model` (see `02_model_training.ipynb`'s "LSTM Model Definition" cell,
    and its preceding "Design Decision: Persistent (`stateful=True`) Hidden State" markdown
    cell for why this was deliberately not done) are plain `tf.keras.layers.LSTM`, *not* built
    with `stateful=True`. There is no persistent RNN
    hidden/cell state carried between calls. What's actually persistent is
    `feature_buffer` — a raw ring buffer of the last `max_timesteps` (60) frames' geometric +
    blendshape features, held as a `tf.Variable` inside the model (see `lstm_model.py`).
    Every single call re-runs the *entire* two-layer LSTM stack over all 60 buffered frames
    from a fresh zero-initialized hidden state — there's no incremental/O(1)-per-frame update
    happening under the hood. In other words: this already **is** the "reprocess a 60-frame
    sliding window every frame" approach, not an alternative to it — there's no cheaper
    stateful-RNN mode available without retraining and re-saving the model with a different
    architecture. In practice this is a small model (two LSTM layers of 64/32 units over 60×58
    floats, output layer sized to 3 classes) so the recompute is cheap even on Pi 5 CPU — but if
    a future profiling pass on real Pi hardware finds this the bottleneck at the target 10 FPS, this is
    the reason, not a bug in `predict_frame`.

    `predict_frame()` must be called once per incoming frame, in order — not accumulated into
    a window by the caller first, since the model already does that internally via
    `feature_buffer`.
    """

    def __init__(self, model: tf.keras.Model):
        self._model = model

    @classmethod
    def from_path(cls, model_path: str | os.PathLike) -> "DrowsinessDetector":
        """Load an already-downloaded `.keras` artifact from an explicit path."""
        model_path = Path(model_path)
        logger.info("Loading drowsiness model from %s", model_path)
        model = tf.keras.models.load_model(
            model_path,
            custom_objects={
                "GeometricRatioFeatureLayer": GeometricRatioFeatureLayer,
                "LstmGeometricFeatureModel": LstmGeometricFeatureModel,
            },
            compile=False,  # inference only — no optimizer/loss state needed
        )
        return cls(model)

    @classmethod
    def from_env(cls) -> "DrowsinessDetector":
        """Download the model per `MODEL_DRIVE_FILE_ID`/`MODEL_DIR`/`MODEL_FILENAME` (see
        `downloader.py`), then load it. This is what `main()` should call in production."""
        model_path = download_model()
        return cls.from_path(model_path)

    def predict_frame(
        self,
        landmarks_xy: np.ndarray,
        rotation_matrix: np.ndarray,
        blendshape_scores: dict[str, float],
    ) -> DetectionResult:
        """Feed one frame's raw features into the model and return the current prediction.

        Args:
            landmarks_xy: (478, 2) normalized (x, y) landmark coordinates.
            rotation_matrix: (3, 3) top-left block of MediaPipe's facial transformation
                matrix — use `np.eye(3)` for a frame where pose isn't available.
            blendshape_scores: category_name -> score, as returned by MediaPipe's
                `face_blendshapes` for one face. Missing names default to 0.0, matching the
                notebook's handling of frames where blendshapes weren't produced.
        """
        landmarks_tf = tf.constant(landmarks_xy, dtype=tf.float32)[tf.newaxis, ...]
        rotation_tf = tf.constant(rotation_matrix, dtype=tf.float32)[tf.newaxis, ...]
        blendshapes_tf = tf.constant(
            [blendshape_scores.get(name, 0.0) for name in GeometricRatioFeatureLayer.blendshape_names],
            dtype=tf.float32,
        )[tf.newaxis, ...]

        model_inputs = {
            "landmarks": landmarks_tf,
            "rotation_matrix": rotation_tf,
            "blendshapes": blendshapes_tf,
        }
        probabilities = self._model(model_inputs, training=False).numpy()[0]
        level = int(np.argmax(probabilities)) + _LEVEL_OFFSET
        return DetectionResult(level=level, probabilities=probabilities)

    def reset(self) -> None:
        """Zero the model's internal sliding-window buffer.

        This buffer (raw geometric+blendshape features for the trailing `max_timesteps`
        frames) is the model's *only* memory — see this class's "how the state
        actually works" note above. Not called automatically anywhere; the model works fine left
        alone, since the buffer is just a ring of the last N frames regardless. This is here
        for a caller that wants a clean slate after a long face-loss gap (e.g. driver stepped
        out at a rest stop), so stale pre-gap frames don't linger in the window and skew the
        first predictions after the driver returns.
        """
        self._model.feature_buffer.assign(tf.zeros_like(self._model.feature_buffer))
