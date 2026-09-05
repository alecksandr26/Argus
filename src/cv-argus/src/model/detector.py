"""Shared `DetectionResult` — the one prediction type every detector in `model/` returns.

Historical note: this module used to also hold `DrowsinessDetector`, the windowed-geometric-only
LSTM detector (`GeometricRatioFeatureLayer` + `LstmGeometricFeatureModel`, trained in
`notebook/03_model_training_lstm.ipynb`), and `_CLASS_NAMES` was the shared 3-class
Alert/Low Vigilant/Drowsy map every detector rendered through. Both were removed once
`PIPELINE=fused` (see `fused_detector.py`) was made this module's sole deployed pipeline — see
the root `CLAUDE.md` and `src/cv-argus/CLAUDE.md`'s "Current status" for why. `layers.py`
(`GeometricRatioFeatureLayer`) is still around and still used, just not through that removed
class — `fused_features.py` calls it directly for its EAR/MAR math.
"""

from dataclasses import dataclass

import numpy as np

# 0-indexed softmax output -> the model's 1-2 drowsiness level (1 = Not Drowsy, 2 = Drowsy).
_LEVEL_OFFSET = 1
_CLASS_NAMES = {1: "Not Drowsy", 2: "Drowsy"}


@dataclass
class DetectionResult:
    """One frame's prediction. `level` is already offset back to the 1-2 scale (1 = Not Drowsy,
    2 = Drowsy)."""

    level: int
    probabilities: np.ndarray  # shape (2,), softmax over [Not Drowsy, Drowsy] in order

    @property
    def class_name(self) -> str:
        """Human-readable label for `level`."""
        return _CLASS_NAMES.get(self.level, f"Unknown({self.level})")
