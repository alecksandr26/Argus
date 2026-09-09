"""`model/fused_detector.py` — `FusedDrowsinessDetector`, the deployed model's per-frame
wrapper and the one piece of this module that holds real state (the sliding window buffer).

The loaded `.keras` graph is stateless; the window lives on this Python object as a plain
numpy array. These tests use **stub** callables for the fused model and the CNN embedder (no
real `.keras`), so they pin the wrapper's own logic: the zero-*pre*-pad ring buffer, the crop
resize, the `p(Drowsy) >= threshold` decision, phase timing, and `reset()`.
"""

import numpy as np
import pytest

tf = pytest.importorskip("tensorflow")

from cv_argus.model.detector import DetectionResult  # noqa: E402
from cv_argus.model.fused_detector import FusedDrowsinessDetector, _force_no_cudnn  # noqa: E402

EMBED_DIM = 4
NUM_GEO = 3
MAX_T = 4


class _StubEmbedder:
    """Stands in for `CnnDrowsinessDetector.embedding_submodel()`. Returns a constant vector
    (`fill`) so a test can predict exactly what lands in the buffer, and records every call's
    input tensor shape."""

    def __init__(self, fill=0.0):
        self.fill = fill
        self.seen_shapes = []

    def __call__(self, image, training=False):
        self.seen_shapes.append(tuple(image.shape))
        return tf.fill((1, EMBED_DIM), float(self.fill))


class _StubFusedModel:
    """Stands in for the loaded fused `.keras` model. Returns a fixed 2-class probability
    vector and records the (buffer, mask) batch it was handed."""

    def __init__(self, prob=(0.5, 0.5)):
        self.prob = prob
        self.received = []

    def __call__(self, inputs, training=False):
        buffer_batch, mask_batch = inputs
        self.received.append((np.asarray(buffer_batch), np.asarray(mask_batch)))
        return tf.constant([self.prob], dtype=tf.float32)


def _make_detector(*, prob=(0.5, 0.5), embed_fill=0.0, threshold=0.5):
    embedder = _StubEmbedder(fill=embed_fill)
    model = _StubFusedModel(prob=prob)
    detector = FusedDrowsinessDetector(
        fused_model=model,
        cnn_embedder=embedder,
        threshold=threshold,
        drowsy_index=1,
        max_timesteps=MAX_T,
        embed_dim=EMBED_DIM,
        num_geo_features=NUM_GEO,
    )
    return detector, model, embedder


def _geo(value):
    return np.full(NUM_GEO, float(value), dtype=np.float32)


def _crop(h=120, w=90):
    return np.zeros((h, w, 3), dtype=np.uint8)


def test_fresh_buffer_is_zero_and_unmasked():
    detector, _, _ = _make_detector()
    assert detector._buffer.shape == (MAX_T, EMBED_DIM + NUM_GEO)
    assert not detector._buffer.any()
    assert detector._mask.shape == (MAX_T,)
    assert not detector._mask.any()


def test_one_frame_lands_in_the_last_slot():
    detector, _, _ = _make_detector(embed_fill=7.0)
    detector.predict_frame(_crop(), _geo(3.0))

    expected_row = np.concatenate([np.full(EMBED_DIM, 7.0), np.full(NUM_GEO, 3.0)])
    assert np.allclose(detector._buffer[-1], expected_row)
    assert not detector._buffer[:-1].any()          # everything before is still zero-pre-pad
    assert detector._mask.tolist() == [False, False, False, True]


def test_buffer_is_zero_pre_padded_newest_last():
    detector, _, _ = _make_detector()
    detector.predict_frame(_crop(), _geo(1.0))
    detector.predict_frame(_crop(), _geo(2.0))

    geo_slice = detector._buffer[:, EMBED_DIM:]      # the geo columns carry our marker value
    assert np.allclose(geo_slice[0], 0.0)           # oldest two slots: still padding
    assert np.allclose(geo_slice[1], 0.0)
    assert np.allclose(geo_slice[2], 1.0)           # first real frame
    assert np.allclose(geo_slice[3], 2.0)           # newest frame, last slot


def test_buffer_fills_and_caps_after_max_timesteps():
    detector, _, _ = _make_detector()
    for k in range(1, MAX_T + 3):                    # overrun the window
        detector.predict_frame(_crop(), _geo(k))

    assert detector._mask.all()
    geo_slice = detector._buffer[:, EMBED_DIM:]
    # last MAX_T frames only: values (MAX_T+2) .. (MAX_T-1)+... i.e. 3,4,5,6 for MAX_T=4
    last_values = [geo_slice[i, 0] for i in range(MAX_T)]
    assert last_values == pytest.approx([3.0, 4.0, 5.0, 6.0])


def test_crop_is_resized_before_embedding():
    detector, _, embedder = _make_detector()
    detector.predict_frame(_crop(h=200, w=57), _geo(0.0))
    assert embedder.seen_shapes == [(1, 96, 96, 3)]  # CNN_IMG_SIZE square, batch of 1


def test_drowsy_when_probability_meets_threshold():
    detector, _, _ = _make_detector(prob=(0.3, 0.7), threshold=0.5)
    result = detector.predict_frame(_crop(), _geo(0.0))
    assert isinstance(result, DetectionResult)
    assert result.level == 2
    assert result.class_name == "Drowsy"
    assert np.allclose(result.probabilities, [0.3, 0.7])


def test_not_drowsy_below_threshold():
    detector, _, _ = _make_detector(prob=(0.6, 0.4), threshold=0.5)
    assert detector.predict_frame(_crop(), _geo(0.0)).level == 1


def test_threshold_boundary_counts_as_drowsy():
    detector, _, _ = _make_detector(prob=(0.5, 0.5), threshold=0.5)  # p == t
    assert detector.predict_frame(_crop(), _geo(0.0)).level == 2


def test_phase_timings_recorded():
    detector, _, _ = _make_detector()
    assert detector.last_phase_seconds == {}
    detector.predict_frame(_crop(), _geo(0.0))
    assert set(detector.last_phase_seconds) == {"embed", "lstm"}
    assert all(v >= 0.0 for v in detector.last_phase_seconds.values())


def test_model_receives_batched_buffer_and_mask():
    detector, model, _ = _make_detector()
    detector.predict_frame(_crop(), _geo(0.0))
    buffer_batch, mask_batch = model.received[-1]
    assert buffer_batch.shape == (1, MAX_T, EMBED_DIM + NUM_GEO)
    assert mask_batch.shape == (1, MAX_T)
    assert mask_batch[0].tolist() == [False, False, False, True]


def test_reset_clears_state():
    detector, _, _ = _make_detector()
    for k in range(MAX_T):
        detector.predict_frame(_crop(), _geo(k + 1))
    assert detector._buffer.any() and detector._mask.any()

    detector.reset()
    assert not detector._buffer.any()
    assert not detector._mask.any()


def test_force_no_cudnn_flips_the_lstm_flag():
    model = tf.keras.Sequential(
        [tf.keras.layers.Input(shape=(5, 3)), tf.keras.layers.LSTM(4)]
    )
    lstm_layer = model.layers[0]
    lstm_layer.use_cudnn = True
    _force_no_cudnn(model)
    assert lstm_layer.use_cudnn is False
