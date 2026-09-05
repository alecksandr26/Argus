"""Cross-check `cv_argus.model.fused_features.compute_fused_geo_features()` (the production code
`pipeline.face_landmarker_crop_stage.FaceLandmarkerCropStage` calls live) against
`argus_dataset`'s own, already-validated implementation of the same 10-feature fusion set.

Three checks, in increasing order of what they actually exercise:
  1. The two packages' feature-name lists must be byte-identical, in order (config.GEO_FEATURE_NAMES
     vs. cv_argus.model.fused_features.FUSED_GEO_FEATURE_NAMES) — cheap drift insurance.
  2. Unit-level: feed the same random (landmarks, rotation, blendshapes) triples through both
     packages' post-processing and compare the resulting 10-vectors.
  3. End-to-end (skipped unless mediapipe + a cached FaceLandmarker bundle + real face-crop
     images are all locally available — no download is triggered by this test): run
     `argus_dataset.pipelines.extract_geo_for_crop()` (the real function that produced
     `09`'s training CSVs) and the `cv_argus` equivalent side by side on the same crop files.

Same loading trick as `test_geometry_equiv.py` (skip unless TensorFlow + the sibling `cv-argus`
package are importable), extended one step further: `cv_argus.model.fused_features` has a
relative import (`from .layers import GeometricRatioFeatureLayer`) that a bare
`spec_from_file_location` can't resolve on its own, so both modules are loaded into a small fake
parent package registered in `sys.modules` first.
"""

import importlib.util
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from argus_dataset import config, geometry

tf = pytest.importorskip("tensorflow")

_MODEL_DIR = Path(__file__).resolve().parents[2] / "cv-argus" / "src" / "model"
_PKG_NAME = "_cvargus_model_for_fused_equiv_test"


def _load_cvargus_model_module(modname: str):
    """Load `<_MODEL_DIR>/<modname>.py` under a fake parent package so its own relative imports
    (e.g. `fused_features.py`'s `from .layers import ...`) resolve against modules already
    loaded the same way, without needing `cv_argus` installed (mediapipe/gdown and friends)."""
    full_name = f"{_PKG_NAME}.{modname}"
    spec = importlib.util.spec_from_file_location(full_name, _MODEL_DIR / f"{modname}.py")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = _PKG_NAME
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


try:
    if _PKG_NAME not in sys.modules:
        _pkg = types.ModuleType(_PKG_NAME)
        _pkg.__path__ = [str(_MODEL_DIR)]
        sys.modules[_PKG_NAME] = _pkg
    _layers_mod = _load_cvargus_model_module("layers")
    _fused_features_mod = _load_cvargus_model_module("fused_features")
    GeometricRatioFeatureLayer = _layers_mod.GeometricRatioFeatureLayer
    compute_fused_geo_features = _fused_features_mod.compute_fused_geo_features
    FUSED_GEO_FEATURE_NAMES = _fused_features_mod.FUSED_GEO_FEATURE_NAMES
except Exception:  # pragma: no cover
    pytest.skip("cv_argus.model.fused_features not importable", allow_module_level=True)


def _random_rotation(rng):
    a, b, c = rng.uniform(-np.pi, np.pi, 3)
    Rx = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
    Ry = np.array([[np.cos(b), 0, np.sin(b)], [0, 1, 0], [-np.sin(b), 0, np.cos(b)]])
    Rz = np.array([[np.cos(c), -np.sin(c), 0], [np.sin(c), np.cos(c), 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def test_feature_name_lists_match():
    """Cheap insurance against the two packages' 10-feature lists silently drifting apart."""
    assert FUSED_GEO_FEATURE_NAMES == config.GEO_FEATURE_NAMES


def test_matches_argus_dataset_within_tol():
    """Random (landmarks, rotation, blendshapes) triples through both packages' post-processing
    of GeometricRatioFeatureLayer's output -- argus_dataset's dict-based selection (mirroring
    `extract_geo_for_crop`'s own logic) vs. cv_argus's `compute_fused_geo_features()`."""
    rng = np.random.default_rng(0)
    # A handful of real blendshape category names this feature set actually selects, plus a few
    # unrelated ones (mirroring what a real FaceLandmarker result dict looks like -- 52 entries,
    # only 7 of which this 10-feature set cares about) so the "look up by name, default missing
    # to 0.0" path is exercised the same way it would be against a real MediaPipe result.
    needed_blendshapes = [n for n in config.GEO_FEATURE_NAMES if n not in config.GEOMETRIC_FEATURE_NAMES]
    extra_blendshapes = ["jawOpen", "mouthClose", "cheekPuff"]

    for _ in range(200):
        landmarks = rng.uniform(0.0, 1.0, size=(478, 2)).astype(np.float64)
        R = _random_rotation(rng)
        blendshape_scores = {
            name: float(rng.uniform(0.0, 1.0)) for name in needed_blendshapes + extra_blendshapes
        }

        # argus_dataset's side: geometry.compute_geometric_features (already cross-checked
        # against the real tf layer by test_geometry_equiv.py) -> dict -> the same
        # dict-lookup-by-name selection extract_geo_for_crop uses.
        geo = geometry.compute_geometric_features(landmarks, R)
        base = dict(zip(config.GEOMETRIC_FEATURE_NAMES, geo.tolist()))
        all_feats = {**base, **blendshape_scores}
        theirs = np.array(
            [float(all_feats.get(name, 0.0)) for name in config.GEO_FEATURE_NAMES], dtype=np.float32
        )

        # cv_argus's side: the real production function.
        mine = compute_fused_geo_features(landmarks, R, blendshape_scores)

        assert np.allclose(mine, theirs, atol=1e-4), (mine, theirs)


def _find_first(root: Path, pattern: str) -> Path | None:
    if not root.exists():
        return None
    return next(root.rglob(pattern), None)


def test_matches_argus_dataset_end_to_end_on_real_crops():
    """Run the real FaceLandmarker (IMAGE mode) on real saved crop files through both
    `argus_dataset.pipelines.extract_geo_for_crop()` and the `cv_argus`-equivalent call path,
    and compare. Skipped (not failed) unless mediapipe, a locally cached Face Landmarker
    `.task` bundle, and at least one real crop `.jpg` are all already present -- this test
    never triggers a network download itself.
    """
    pytest.importorskip("mediapipe")
    import mediapipe as mp

    from argus_dataset import pipelines as ds_pipelines

    dataset_root = Path(__file__).resolve().parents[1]
    bundle_path = _find_first(dataset_root, "face_landmarker.task") or _find_first(
        Path.home(), "face_landmarker.task"
    )
    crop_path = _find_first(dataset_root / "processed", "*.jpg")
    if bundle_path is None or crop_path is None:
        pytest.skip(
            "no locally cached face_landmarker.task and/or real face-crop .jpg found -- "
            "this test doesn't download either, run the dataset pipeline first to exercise it"
        )

    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    options = vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(bundle_path)),
        running_mode=vision.RunningMode.IMAGE,
        num_faces=1,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
        min_tracking_confidence=0.5,
        output_face_blendshapes=True,
        output_facial_transformation_matrixes=True,
    )
    landmarker = vision.FaceLandmarker.create_from_options(options)
    try:
        theirs = ds_pipelines.extract_geo_for_crop(str(crop_path), landmarker)

        import cv2

        bgr = cv2.imread(str(crop_path))
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        result = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
        assert result.face_landmarks, f"no face detected in {crop_path} -- pick a different fixture crop"
        landmarks_xy = np.array([[lm.x, lm.y] for lm in result.face_landmarks[0]], dtype=np.float32)
        rotation_matrix = (
            np.array(result.facial_transformation_matrixes[0], dtype=np.float32)[:3, :3]
            if result.facial_transformation_matrixes
            else np.eye(3, dtype=np.float32)
        )
        blendshape_scores = (
            {b.category_name: b.score for b in result.face_blendshapes[0]}
            if result.face_blendshapes
            else {}
        )
        mine = compute_fused_geo_features(landmarks_xy, rotation_matrix, blendshape_scores)

        assert np.allclose(mine, theirs, atol=1e-4), (mine, theirs)
    finally:
        landmarker.close()
