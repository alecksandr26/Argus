"""Training/inference fidelity for the parts detector: cv-argus's square face crop
(`model/parts_geometry.py`) must equal the crop `detection_dataset.py` built the training data
with. The two are deliberately separate copies (cv-argus doesn't import src/dataset), so this
test is the contract. A drift silently shows the model a different framing than it learned on.

cv-argus is loaded from source under bare namespace packages, so its heavy `model/__init__`
(TensorFlow) never runs. Skipped if the sibling package or OpenCV isn't there.
"""

import importlib
import sys
import types
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

_CV_SRC = Path(__file__).resolve().parents[2] / "cv-argus" / "src"


@pytest.fixture(scope="module")
def cv_geom():
    if not (_CV_SRC / "model" / "parts_geometry.py").exists():
        pytest.skip("src/cv-argus not present")
    saved = {k: sys.modules.get(k) for k in ("cv_argus", "cv_argus.model", "cv_argus.constants",
                                             "cv_argus.model.parts_geometry")}
    pkg = types.ModuleType("cv_argus"); pkg.__path__ = [str(_CV_SRC)]
    sub = types.ModuleType("cv_argus.model"); sub.__path__ = [str(_CV_SRC / "model")]
    sys.modules.update({"cv_argus": pkg, "cv_argus.model": sub})
    try:
        yield importlib.import_module("cv_argus.model.parts_geometry")
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def test_constants_match(cv_geom):
    from argus_dataset import config
    c = cv_geom.constants
    assert c.PARTS_CLASSES == config.DET_CLASSES
    assert c.PARTS_STORED_CROP_SIZE == config.DET_IMG_SIZE
    assert c.PARTS_FACE_MARGIN_FRAC == config.DET_FACE_MARGIN_FRAC


def test_square_box_and_crop_are_identical(cv_geom):
    from argus_dataset import detection_dataset as dd
    rng = np.random.default_rng(0)
    img = rng.integers(0, 255, (480, 640, 3), dtype=np.uint8)
    for _ in range(50):
        fx, fy = rng.uniform(-80, 600), rng.uniform(-80, 440)
        fw, fh = rng.uniform(20, 300), rng.uniform(20, 300)
        a = dd.square_face_box(fx, fy, fw, fh)
        b = cv_geom.square_face_box(fx, fy, fw, fh)
        assert a == pytest.approx(b)
        np.testing.assert_array_equal(dd.crop_square(img, *a, 192), cv_geom.crop_square(img, *b, 192))
