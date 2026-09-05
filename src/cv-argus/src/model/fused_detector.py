"""Loads `best_cnn_lstm_frozen_embedding.keras` (the frozen-CNN-embedding + geometric-feature
fusion + LSTM classifier from `notebook/11_cnn_lstm_training_drive_pull.ipynb`) and wraps it in
a per-frame `predict_frame()` call — this package's sole deployed model.

**The loaded `.keras` graph has no internal state at all** — confirmed against the notebook:
it's built from stock `Input`/`Normalization`/`LSTM`/`Dropout`/`Dense` layers only, taking a
pre-assembled `(max_timesteps, fused_dim)` array and a `(max_timesteps,)` boolean mask as two
*external* inputs. The ring buffer therefore has to live on *this* Python wrapper instead, as a
plain `numpy` array — one `FusedDrowsinessDetector` per camera stream. `predict_frame()` must be
called once per incoming frame, in order, since that ring buffer is this class's only memory.

The buffer holds **numbers, not images**: each frame's face crop is fed through the frozen CNN
embedder once, immediately reduced to a 64-float embedding, fused with a 10-float geometric
vector into one 74-float row, and only that row is kept — the crop's pixels are discarded right
away. A 100-row buffer is ~30KB, nowhere near the cost of buffering 100 crop images and
re-running the CNN over the whole window on every tick (which would also be wrong: the CNN's
output for an old, already-embedded frame never changes, so recomputing it would be pure waste).

Deliberately has no `mediapipe` import, the same boundary `detector.py`/`cnn_detector.py` draw:
`predict_frame()` takes an already-cropped RGB image and an already-computed geometric-feature
vector (see `fused_features.py`), not any MediaPipe result object. Producing those two things is
`pipeline/`'s job (`FaceDetectorCropStage` + `FaceLandmarkerCropStage`).
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tensorflow as tf

from .. import constants
from .cnn_detector import CnnDrowsinessDetector
from .detector import DetectionResult, _LEVEL_OFFSET
from .downloader import download_cnn_model, download_fused_model
from .fused_features import NUM_FUSED_GEO_FEATURES

logger = logging.getLogger(__name__)


@dataclass
class _LoadedFusedModels:
    """Just a container so `from_path`/`from_env` (which both need to hand `__init__` a fused
    model *and* a CNN embedder) don't have two separate multi-return-value call sites to keep in
    sync."""

    fused_model: tf.keras.Model
    cnn_embedder: tf.keras.Model


def _force_no_cudnn(model: tf.keras.Model) -> None:
    """The fused model's sequences are zero-*pre*-padded (zeros first, real frames last) --
    Keras' cuDNN-accelerated LSTM fast path assumes right-padding and silently mishandles this
    convention otherwise. Notebook 11's own `evaluate_variant`/`_load_for_eval` re-applies this
    on every loaded checkpoint for the same reason; irrelevant for raw speed on a Pi (no cuDNN
    there anyway) but required for correctness, not just a training-time nicety."""
    for layer in model.layers:
        if isinstance(layer, tf.keras.layers.LSTM):
            layer.use_cudnn = False


def _load_fused_models(fused_model_path: str | os.PathLike, cnn_model_path: str | os.PathLike) -> _LoadedFusedModels:
    logger.info("Loading fused CNN+LSTM model from %s", fused_model_path)
    # No custom_objects needed: Input/Normalization/LSTM/Dropout/Dense only -- confirmed
    # against the notebook, no custom Layer/Model subclass in this graph.
    fused_model = tf.keras.models.load_model(fused_model_path, compile=False)
    _force_no_cudnn(fused_model)

    logger.info("Loading frozen CNN embedder from %s", cnn_model_path)
    cnn_embedder = CnnDrowsinessDetector.from_path(cnn_model_path).embedding_submodel()

    return _LoadedFusedModels(fused_model=fused_model, cnn_embedder=cnn_embedder)


class FusedDrowsinessDetector:
    """Loads the fused CNN-embedding + geometric-feature + LSTM classifier and turns a stream of
    (face crop, geometric-feature vector) pairs into drowsiness predictions.

    Unlike `CnnDrowsinessDetector` (a stateless checkpoint/embedding loader), this has real
    state — the window buffer — and must be called in frame order; see this module's docstring
    for how that state is implemented (a plain numpy array on this class, not inside the
    `.keras` graph).
    """

    def __init__(
        self,
        fused_model: tf.keras.Model,
        cnn_embedder: tf.keras.Model,
        threshold: float,
        drowsy_index: int = 1,
        max_timesteps: int = constants.FUSED_MODEL_MAX_TIMESTEPS,
        embed_dim: int = constants.FUSED_MODEL_EMBED_DIM,
        num_geo_features: int = NUM_FUSED_GEO_FEATURES,
    ) -> None:
        self._model = fused_model
        self._embedder = cnn_embedder
        self._threshold = threshold
        self._drowsy_index = drowsy_index
        self._max_timesteps = max_timesteps
        self._fused_dim = embed_dim + num_geo_features

        # The window buffer -- plain numpy, not a tf.Variable inside the model (see module
        # docstring). Zero-pre-padded: index 0 is the "oldest slot", index -1 the newest: a
        # fresh detector's buffer is all-zeros/all-masked-out, exactly matching how a real
        # window looks before it's had max_timesteps frames fed into it.
        self._buffer = np.zeros((max_timesteps, self._fused_dim), dtype=np.float32)
        self._mask = np.zeros((max_timesteps,), dtype=bool)

    @classmethod
    def from_path(
        cls,
        fused_model_path: str | os.PathLike,
        cnn_model_path: str | os.PathLike,
        threshold: float | None = None,
    ) -> "FusedDrowsinessDetector":
        """Load both already-downloaded `.keras` artifacts from explicit paths.

        `threshold` defaults to `constants.FUSED_MODEL_THRESHOLD` (the value
        `<checkpoint>.keras.threshold.json` recorded at training time, checked into this repo
        as a constant rather than fetched as a third Drive artifact -- see
        `src/cv-argus/CLAUDE.md`'s fused-pipeline section for why).
        """
        loaded = _load_fused_models(fused_model_path, cnn_model_path)
        return cls(
            fused_model=loaded.fused_model,
            cnn_embedder=loaded.cnn_embedder,
            threshold=threshold if threshold is not None else constants.FUSED_MODEL_THRESHOLD,
            drowsy_index=constants.FUSED_MODEL_DROWSY_INDEX,
        )

    @classmethod
    def from_env(cls) -> "FusedDrowsinessDetector":
        """Download both required artifacts (the fused model per `FUSED_MODEL_DRIVE_FILE_ID`,
        the CNN per `CNN_MODEL_DRIVE_FILE_ID` -- the fused model's embeddings must come from
        that exact checkpoint's weights, not a different training run -- see
        `src/cv-argus/CLAUDE.md`'s "Current status" for the still-open provenance risk), then
        load them. This is what `main()` calls."""
        fused_model_path = download_fused_model()
        cnn_model_path = download_cnn_model()
        return cls.from_path(fused_model_path, cnn_model_path)

    def predict_frame(self, face_crop_rgb: np.ndarray, geo_features: np.ndarray) -> DetectionResult:
        """Feed one frame's face crop + already-computed 10-dim geometric-feature vector
        (`fused_features.compute_fused_geo_features()`, or
        `fused_features.zero_fused_geo_features()` on a landmarker miss) into the model.

        Args:
            face_crop_rgb: an (H, W, 3) RGB image, pixel values in `[0, 255]`, any size --
                resized to `(CNN_IMG_SIZE, CNN_IMG_SIZE)` internally before the embedder runs.
            geo_features: shape `(NUM_FUSED_GEO_FEATURES,)` float32.
        """
        image = tf.convert_to_tensor(face_crop_rgb, dtype=tf.float32)
        image = tf.image.resize(image, [constants.CNN_IMG_SIZE, constants.CNN_IMG_SIZE])
        image = image[tf.newaxis, ...]
        embedding = self._embedder(image, training=False).numpy()[0]  # (embed_dim,)

        fused_frame = np.concatenate([embedding, geo_features]).astype(np.float32)  # (fused_dim,)

        # Shift the buffer left (drop the oldest row) and insert the new frame at the LAST slot
        # -- the zero-pre-pad convention the model was trained on (zeros first, real frames
        # last), the opposite of a naive "append at the front, drop from the back" ring buffer.
        self._buffer = np.roll(self._buffer, shift=-1, axis=0)
        self._buffer[-1] = fused_frame
        self._mask = np.roll(self._mask, shift=-1)
        self._mask[-1] = True

        probabilities = self._model(
            [self._buffer[np.newaxis, ...], self._mask[np.newaxis, ...]], training=False
        ).numpy()[0]

        is_drowsy = bool(probabilities[self._drowsy_index] >= self._threshold)
        level = int(is_drowsy) + _LEVEL_OFFSET  # 1 = Not Drowsy, 2 = Drowsy
        return DetectionResult(level=level, probabilities=probabilities)

    def reset(self) -> None:
        """Zero the window buffer and mask. Not called automatically -- here for a caller that
        wants a clean slate after a long face-loss gap so stale pre-gap frames don't linger and
        skew the first predictions after the driver returns."""
        self._buffer[:] = 0.0
        self._mask[:] = False
