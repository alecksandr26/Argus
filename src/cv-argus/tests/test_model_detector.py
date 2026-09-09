"""`model/detector.py` — the shared `DetectionResult` type and its 1/2 level ↔ class-name map."""

import numpy as np

from cv_argus.model.detector import _CLASS_NAMES, _LEVEL_OFFSET, DetectionResult


def test_class_names_are_the_binary_scheme():
    assert _CLASS_NAMES == {1: "Not Drowsy", 2: "Drowsy"}
    assert _LEVEL_OFFSET == 1


def test_class_name_for_known_levels():
    assert DetectionResult(level=1, probabilities=np.array([0.9, 0.1])).class_name == "Not Drowsy"
    assert DetectionResult(level=2, probabilities=np.array([0.1, 0.9])).class_name == "Drowsy"


def test_class_name_unknown_level_falls_back_readably():
    assert DetectionResult(level=7, probabilities=np.array([0.0, 0.0])).class_name == "Unknown(7)"


def test_level_offset_maps_softmax_index_to_level():
    # index 0 (Not Drowsy) -> level 1; index 1 (Drowsy) -> level 2
    for softmax_index in (0, 1):
        assert softmax_index + _LEVEL_OFFSET in _CLASS_NAMES
