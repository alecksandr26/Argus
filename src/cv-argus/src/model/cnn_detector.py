"""Loads the trained CNN face-crop model (`notebook/07_cnn_training.ipynb`) and exposes its
penultimate layer as a frozen embedding sub-model — the feature extractor `FusedDrowsinessDetector`
(`fused_detector.py`) fuses with a geometric-feature vector before feeding an LSTM.

This CNN is no longer run for its own single-frame classification in this codebase (that was the
now-removed `PIPELINE=cnn`, superseded by `PIPELINE=fused`'s much stronger windowed result — see
the root `CLAUDE.md` and `src/cv-argus/CLAUDE.md`'s "Current status"). Its trained weights are
still needed, though: `FusedDrowsinessDetector`'s per-frame embedding must come from the *exact*
checkpoint `notebook/11_cnn_lstm_training_drive_pull.ipynb` trained its LSTM against, not a
re-derived one — see `embedding_submodel()` below.

Plain, built-in Keras layers only — `Sequential`/`Rescaling`/`Conv2D`/`BatchNormalization`/
`GlobalAveragePooling2D`/`Dense` (see `07`'s model-definition cell) — no custom `Layer`/`Model`
subclasses, so loading it needs no `custom_objects`.

Deliberately has no `mediapipe` import, the same boundary `detector.py` draws for the LSTM
path: this module only ever sees an already-cropped face image, not a MediaPipe detection
result — producing that crop is `pipeline/`'s job.
"""

import logging
import os
from pathlib import Path

import tensorflow as tf

from .. import constants

logger = logging.getLogger(__name__)


class CnnDrowsinessDetector:
    """Loads the trained CNN face-crop classifier and exposes its frozen embedding sub-model.

    No internal state or buffering: this class is now purely a checkpoint loader for
    `embedding_submodel()` — the CNN itself is never called for a standalone prediction anymore
    (see this module's docstring).
    """

    def __init__(self, model: tf.keras.Model):
        self._model = model
        self._embedding_submodel: tf.keras.Model | None = None

    @classmethod
    def from_path(cls, model_path: str | os.PathLike) -> "CnnDrowsinessDetector":
        """Load an already-downloaded `.keras` artifact from an explicit path."""
        model_path = Path(model_path)
        logger.info("Loading CNN drowsiness model from %s", model_path)
        model = tf.keras.models.load_model(model_path, compile=False)
        return cls(model)

    def embedding_submodel(self) -> tf.keras.Model:
        """Build (and cache) a sub-graph exposing this CNN's penultimate `Dense(64, relu)`
        layer — the "frozen CNN embedding" `FusedDrowsinessDetector` (`fused_detector.py`)
        fuses with a geometric-feature vector before feeding an LSTM. Ported verbatim from
        `notebook/11_cnn_lstm_training_drive_pull.ipynb` cell 25.

        This CNN (`notebook/07_cnn_training.ipynb`'s `build_cnn_scratch`) ends
        `... -> GlobalAveragePooling2D -> Dense(64, relu) -> Dropout(0.5) -> Dense(num_classes,
        softmax)`. "The embedding" is the second-to-last `Dense` layer (`dense_layers[-2]`) —
        the 64-dim pre-classification representation, not the final softmax. Kept as a method on
        this class rather than a free function taking a raw `tf.keras.Model`, so "the loaded CNN
        and everything derived from it" stays one owned object — using a *different* CNN's
        weights for the embedding than what `self._model` holds would silently produce
        embeddings the fused LSTM was never trained on.
        """
        if self._embedding_submodel is not None:
            return self._embedding_submodel

        dense_layers = [layer for layer in self._model.layers if isinstance(layer, tf.keras.layers.Dense)]
        if len(dense_layers) < 2:
            raise ValueError(
                f"Expected at least 2 Dense layers in the loaded CNN, found {len(dense_layers)} "
                "-- can't locate the penultimate embedding layer."
            )
        embedding_layer = dense_layers[-2]

        # Force the model to build (assign real input/output shapes) before touching .input on
        # a layer of it -- a freshly-loaded, never-called Sequential raises "model has never
        # been called" otherwise. `model.layers[0].input`, not `model.input` directly, for the
        # same reason (notebook 11 cell 25's own comment on this exact gotcha).
        self._model(tf.zeros((1, constants.CNN_IMG_SIZE, constants.CNN_IMG_SIZE, 3)), training=False)

        self._embedding_submodel = tf.keras.Model(
            inputs=self._model.layers[0].input,
            outputs=embedding_layer.output,
            name="frozen_cnn_embedder",
        )
        return self._embedding_submodel
