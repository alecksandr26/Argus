"""`model/fused_features.py` — the 10-feature geometric subset the fused model fuses with the
CNN embedding.

Order matters: `FUSED_GEO_FEATURE_NAMES` is baked into `best_cnn_lstm_frozen_embedding.keras`'s
expected input layout (slots 0-2 = EAR/MAR from the ratio layer, slots 3-9 = named blendshapes,
missing → 0.0). These tests pin that contract.
"""

import numpy as np
import pytest

tf = pytest.importorskip("tensorflow")

from cv_argus.model import fused_features  # noqa: E402
from cv_argus.model.fused_features import (  # noqa: E402
    FUSED_GEO_FEATURE_NAMES,
    NUM_FUSED_GEO_FEATURES,
    compute_fused_geo_features,
    zero_fused_geo_features,
)
from cv_argus.model.layers import GeometricRatioFeatureLayer  # noqa: E402

_LEFT_EYE = [33, 160, 158, 133, 153, 144]
_RIGHT_EYE = [362, 385, 387, 263, 373, 380]
_MOUTH = [61, 291, 13, 14]


def _landmarks(ear_left, ear_right, mar) -> np.ndarray:
    lm = np.zeros((478, 2), dtype=np.float32)
    for idx, ear in ((_LEFT_EYE, ear_left), (_RIGHT_EYE, ear_right)):
        c0, t1, t2, c3, b2, b1 = idx
        lm[c0], lm[c3] = (0.0, 0.0), (1.0, 0.0)
        lm[t1], lm[b1] = (0.3, ear / 2), (0.3, -ear / 2)
        lm[t2], lm[b2] = (0.6, ear / 2), (0.6, -ear / 2)
    lc, rc, up, lo = _MOUTH
    lm[lc], lm[rc] = (0.0, 0.0), (1.0, 0.0)
    lm[up], lm[lo] = (0.5, mar / 2), (0.5, -mar / 2)
    return lm


def test_name_list_shape():
    assert len(FUSED_GEO_FEATURE_NAMES) == NUM_FUSED_GEO_FEATURES == 10
    assert FUSED_GEO_FEATURE_NAMES[:3] == ["EAR_left", "EAR_right", "MAR"]
    # the 7 blendshape slots must all be real ARKit blendshape names
    for name in FUSED_GEO_FEATURE_NAMES[3:]:
        assert name in GeometricRatioFeatureLayer.blendshape_names


def test_zero_fallback_vector():
    z = zero_fused_geo_features()
    assert z.shape == (10,)
    assert z.dtype == np.float32
    assert not z.any()


def test_compute_places_ratios_then_blendshapes():
    lm = _landmarks(0.25, 0.35, 0.40)
    blendshapes = {name: 0.1 * (i + 1) for i, name in enumerate(FUSED_GEO_FEATURE_NAMES[3:])}
    out = compute_fused_geo_features(lm, np.eye(3, dtype=np.float32), blendshapes)

    assert out.shape == (10,)
    assert out.dtype == np.float32
    assert np.allclose(out[:3], [0.25, 0.35, 0.40], atol=1e-4)
    for i, name in enumerate(FUSED_GEO_FEATURE_NAMES[3:]):
        assert np.isclose(out[3 + i], blendshapes[name], atol=1e-6), name


def test_compute_defaults_missing_blendshape_to_zero():
    lm = _landmarks(0.3, 0.3, 0.3)
    # supply only one blendshape; the other six slots must come back as 0.0
    out = compute_fused_geo_features(lm, np.eye(3, dtype=np.float32), {"eyeBlinkLeft": 0.9})
    blink_left_slot = FUSED_GEO_FEATURE_NAMES.index("eyeBlinkLeft")
    assert np.isclose(out[blink_left_slot], 0.9)
    for i, name in enumerate(FUSED_GEO_FEATURE_NAMES[3:], start=3):
        if name != "eyeBlinkLeft":
            assert out[i] == 0.0


def test_reused_ratio_layer_gives_identical_output():
    lm = _landmarks(0.2, 0.4, 0.5)
    R = np.eye(3, dtype=np.float32)
    bs = {"eyeBlinkLeft": 0.5, "eyeBlinkRight": 0.4}
    shared = GeometricRatioFeatureLayer()
    a = compute_fused_geo_features(lm, R, bs, ratio_layer=shared)
    b = compute_fused_geo_features(lm, R, bs, ratio_layer=shared)
    c = compute_fused_geo_features(lm, R, bs)  # fresh layer built internally
    assert np.array_equal(a, b)
    assert np.allclose(a, c, atol=1e-6)


def test_unknown_blendshape_in_dict_is_ignored():
    lm = _landmarks(0.3, 0.3, 0.3)
    out = compute_fused_geo_features(
        lm, np.eye(3, dtype=np.float32), {"totallyNotABlendshape": 1.0}
    )
    assert out.shape == (10,)
    assert np.allclose(out[3:], 0.0)
