"""Shared `DetectionResult`: the per-frame verdict the orchestrator consumes.

The parts pipeline's rule engine (`orchestrator/parts_rules.to_detection`) builds it: `level` and
`probabilities` keep the binary `Not Drowsy` / `Drowsy` contract the ESP32 reads, and `extras`
carries the severity, reason and timers. (Earlier detectors, the windowed LSTM and the CNN+LSTM,
returned the same type; they're on branch `archive/cnn-lstm-fused`.)
"""

from dataclasses import dataclass, field

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
    # Optional detector-specific context merged into the buffered reading's payload by the
    # orchestrator (the parts pipeline's {severity, reason, timers, ...}). Readers that don't
    # know these keys ignore them, so this is backward compatible.
    extras: dict = field(default_factory=dict)

    @property
    def class_name(self) -> str:
        """Human-readable label for `level`."""
        return _CLASS_NAMES.get(self.level, f"Unknown({self.level})")
