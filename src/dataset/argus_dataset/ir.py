"""Infrared ("NoIR") simulation for RGB training images.

The cabin camera is a Raspberry Pi NoIR OV5647 lit by IR LEDs, so at night it sees monochrome
near-infrared, not colour. The labelled eye/mouth sources (Roboflow, UTA-RLDD) are RGB. This
turns an RGB crop into an IR-like grayscale image in two deterministic steps:

1. **Red-dominant channel mix** (``config.IR_CHANNEL_WEIGHTS_BGR``). Red is the visible band
   closest to NIR: skin goes bright and smooth, irises lighten, pupils stay dark.
2. **Histogram matching** toward a reference intensity distribution built from MRL Eye (real IR
   sensor images), blended at ``config.IR_HIST_MATCH_STRENGTH`` so the crop's own contrast isn't
   erased.

Random IR effects (corneal glints, LED falloff, sensor noise) belong in training-time
augmentation, not here. **Fidelity rule:** this is applied to RGB *training* sources only.
MRL is already IR and is left alone, and real camera frames at inference just go to grayscale.
"""

from __future__ import annotations

import numpy as np

from . import config


def red_mix(bgr: np.ndarray, weights=config.IR_CHANNEL_WEIGHTS_BGR) -> np.ndarray:
    """uint8 HxWx3 BGR → uint8 HxW, red-weighted. A 2-D input is returned unchanged."""
    if bgr.ndim == 2:
        return bgr
    w = np.asarray(weights, np.float32) / np.sum(weights)
    out = bgr[..., :3].astype(np.float32) @ w
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def reference_cdf(gray_images) -> np.ndarray:
    """Normalised 256-bin CDF over the pixels of an iterable of uint8 grayscale images."""
    hist = np.zeros(256, np.float64)
    for g in gray_images:
        hist += np.bincount(np.asarray(g, np.uint8).ravel(), minlength=256)
    if hist.sum() == 0:
        raise ValueError("reference_cdf: no pixels")
    cdf = np.cumsum(hist)
    return cdf / cdf[-1]


def match_to_cdf(gray: np.ndarray, ref_cdf: np.ndarray, strength: float = 1.0) -> np.ndarray:
    """Histogram-match ``gray`` to ``ref_cdf``, blended with the input at ``strength`` ∈ [0,1]."""
    src_cdf = np.cumsum(np.bincount(gray.ravel(), minlength=256)).astype(np.float64)
    src_cdf /= src_cdf[-1]
    lut = np.searchsorted(ref_cdf, src_cdf).clip(0, 255).astype(np.float32)
    matched = lut[gray]
    out = (1.0 - strength) * gray.astype(np.float32) + strength * matched
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def to_ir_like(bgr: np.ndarray, ref_cdf: np.ndarray | None,
               strength: float = config.IR_HIST_MATCH_STRENGTH) -> np.ndarray:
    gray = red_mix(bgr)
    return gray if ref_cdf is None or strength <= 0 else match_to_cdf(gray, ref_cdf, strength)
