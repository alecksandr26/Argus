"""Cross-check the NumPy geometry port against the real tf.keras GeometricRatioFeatureLayer.

Skipped unless TensorFlow is importable. The reference layer is ``reference_geometric_layer.py``
next to this file: a verbatim copy of the notebooks' class (it used to live in cv-argus, which no
longer deploys the CNN+LSTM; see branch archive/cnn-lstm-fused). This is the fidelity guard
that lets the pipeline drop TensorFlow without a 6th verbatim copy of the layer.
"""

import numpy as np
import pytest

from argus_dataset import geometry

tf = pytest.importorskip("tensorflow")

try:
    import importlib.util
    from pathlib import Path

    _layers_py = Path(__file__).resolve().parent / "reference_geometric_layer.py"
    # Load it by path: tests/ is not a package, and this keeps the fixture out of argus_dataset.
    _spec = importlib.util.spec_from_file_location("_cvargus_layers", _layers_py)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    GeometricRatioFeatureLayer = _mod.GeometricRatioFeatureLayer
except Exception:  # pragma: no cover
    pytest.skip("reference GeometricRatioFeatureLayer not importable", allow_module_level=True)


def _random_rotation(rng):
    a, b, c = rng.uniform(-np.pi, np.pi, 3)
    Rx = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
    Ry = np.array([[np.cos(b), 0, np.sin(b)], [0, 1, 0], [-np.sin(b), 0, np.cos(b)]])
    Rz = np.array([[np.cos(c), -np.sin(c), 0], [np.sin(c), np.cos(c), 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def test_matches_tf_layer_within_tol():
    rng = np.random.default_rng(0)
    layer = GeometricRatioFeatureLayer(pose_validity_threshold_deg=20.0)

    for _ in range(200):
        landmarks = rng.uniform(0.0, 1.0, size=(478, 2)).astype(np.float64)
        R = _random_rotation(rng)

        mine = geometry.compute_geometric_features(landmarks, R)
        theirs = layer(
            tf.constant(landmarks[None, ...], tf.float32),
            tf.constant(R[None, ...], tf.float32),
        ).numpy()[0]

        # angles can wrap near +/-180; compare as unit vectors there
        assert np.allclose(mine[:3], theirs[:3], atol=1e-4), (mine[:3], theirs[:3])
        for k in (3, 4, 5):
            assert np.allclose(
                [np.cos(np.deg2rad(mine[k])), np.sin(np.deg2rad(mine[k]))],
                [np.cos(np.deg2rad(theirs[k])), np.sin(np.deg2rad(theirs[k]))],
                atol=1e-4,
            )
        assert mine[6] == theirs[6]
