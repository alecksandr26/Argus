"""Loads the trained CNN face-crop model and wraps it in a per-crop `predict_crop()` call.

Unlike the LSTM path (see `detector.py`), the CNN model is plain, built-in Keras layers only —
`Sequential`/`Rescaling`/`Conv2D`/`BatchNormalization`/`GlobalAveragePooling2D`/`Dense` (see
`notebook/07_cnn_training.ipynb`'s model-definition cell) — no custom `Layer`/`Model`
subclasses, so loading it needs no `custom_objects`, unlike `DrowsinessDetector.from_path()`.

Deliberately has no `mediapipe` import, the same boundary `detector.py` draws for the LSTM
path: `predict_crop()` takes a plain `numpy` array (an already-cropped face image), not a
MediaPipe detection result. Producing that crop — running MediaPipe's Face Detector (BlazeFace)
over a raw frame and cropping its bounding box, the way
`notebook/06_dataset_creation_face_crops.ipynb` does it — is `pipeline/`'s job, not this
module's; see `src/cv-argus/CLAUDE.md` for the current status of that piece.
"""

import logging
import os
from pathlib import Path

import numpy as np
import tensorflow as tf

from .. import constants
from .detector import DetectionResult, _LEVEL_OFFSET
from .downloader import download_cnn_model

logger = logging.getLogger(__name__)


class CnnDrowsinessDetector:
    """Loads the trained CNN face-crop classifier and turns one face crop into a drowsiness
    level.

    No internal state or buffering, unlike `DrowsinessDetector`: the CNN was trained on — and
    only ever sees — a single frame's face crop (see `notebook/07_cnn_training.ipynb`), so
    there's no sliding window to maintain and no `reset()` to call between predictions.
    """

    def __init__(self, model: tf.keras.Model):
        self._model = model

    @classmethod
    def from_path(cls, model_path: str | os.PathLike) -> "CnnDrowsinessDetector":
        """Load an already-downloaded `.keras` artifact from an explicit path."""
        model_path = Path(model_path)
        logger.info("Loading CNN drowsiness model from %s", model_path)
        model = tf.keras.models.load_model(model_path, compile=False)
        return cls(model)

    @classmethod
    def from_env(cls) -> "CnnDrowsinessDetector":
        """Download the model per `CNN_MODEL_DRIVE_FILE_ID`/`MODEL_DIR`/`CNN_MODEL_FILENAME`
        (see `downloader.py`), then load it. This is what `main()` should call in production
        for the CNN path."""
        model_path = download_cnn_model()
        return cls.from_path(model_path)

    def predict_crop(self, face_crop_rgb: np.ndarray) -> DetectionResult:
        """Feed one already-cropped, RGB face image into the model and return the prediction.

        Args:
            face_crop_rgb: an (H, W, 3) RGB image, pixel values in `[0, 255]`, any size — resized
                to `(constants.CNN_IMG_SIZE, constants.CNN_IMG_SIZE)` internally via
                `tf.image.resize`, matching `07_cnn_training.ipynb`'s `load_and_preprocess`
                exactly. Don't pre-normalize before calling this: the model's own first layer
                (`Rescaling(1./255)`) handles the `[0, 255] -> [0, 1]` scaling, the same way it
                does during training.
        """
        image = tf.convert_to_tensor(face_crop_rgb, dtype=tf.float32)
        image = tf.image.resize(image, [constants.CNN_IMG_SIZE, constants.CNN_IMG_SIZE])
        image = image[tf.newaxis, ...]  # batch size 1

        probabilities = self._model(image, training=False).numpy()[0]
        level = int(np.argmax(probabilities)) + _LEVEL_OFFSET
        return DetectionResult(level=level, probabilities=probabilities)
