"""`model/cnn_detector.py` — loads `07`'s CNN checkpoint and exposes its penultimate
`Dense(64, relu)` as a frozen embedding sub-model. That sub-model is the *only* reason the CNN
checkpoint is still downloaded (it is never run for its own classification anymore).

These build a tiny stand-in with the same layer *shape* as `07`'s `build_cnn_scratch`
(`... -> GAP -> Dense(64, relu) -> Dropout -> Dense(n, softmax)`), save it as a real `.keras`
file, and check the extraction picks the right layer.
"""

import numpy as np
import pytest

tf = pytest.importorskip("tensorflow")

from cv_argus import constants  # noqa: E402
from cv_argus.model.cnn_detector import CnnDrowsinessDetector  # noqa: E402

_S = constants.CNN_IMG_SIZE


def _build_cnn(tmp_path, *, n_dense_before_head: int = 1, filename: str = "cnn.keras"):
    """Sequential shaped like `07`'s scratch CNN. `n_dense_before_head=1` gives the real shape
    (one Dense(64) embedding layer + the softmax head = 2 Dense total)."""
    layers = [tf.keras.layers.Input(shape=(_S, _S, 3))]
    layers += [
        tf.keras.layers.Rescaling(1.0 / 255),
        tf.keras.layers.Conv2D(8, 3, activation="relu"),
        tf.keras.layers.GlobalAveragePooling2D(),
    ]
    for _ in range(n_dense_before_head):
        layers.append(tf.keras.layers.Dense(64, activation="relu"))
        layers.append(tf.keras.layers.Dropout(0.5))
    layers.append(tf.keras.layers.Dense(2, activation="softmax"))
    model = tf.keras.Sequential(layers)
    path = tmp_path / filename
    model.save(path)
    return path


def test_embedding_submodel_is_the_penultimate_dense(tmp_path):
    detector = CnnDrowsinessDetector.from_path(_build_cnn(tmp_path))
    embedder = detector.embedding_submodel()

    out = embedder(np.zeros((1, _S, _S, 3), dtype=np.float32), training=False)
    assert tuple(out.shape) == (1, 64)  # the Dense(64) embedding, not the 2-way softmax


def test_embedding_submodel_is_cached(tmp_path):
    detector = CnnDrowsinessDetector.from_path(_build_cnn(tmp_path))
    assert detector.embedding_submodel() is detector.embedding_submodel()


def test_fewer_than_two_dense_layers_raises(tmp_path):
    # only the softmax head -> a single Dense -> can't locate a penultimate embedding layer
    path = _build_cnn(tmp_path, n_dense_before_head=0, filename="tiny.keras")
    detector = CnnDrowsinessDetector.from_path(path)
    with pytest.raises(ValueError, match="at least 2 Dense"):
        detector.embedding_submodel()
