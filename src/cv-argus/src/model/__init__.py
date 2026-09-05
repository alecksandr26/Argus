"""Model loading and inference for the cv-argus edge pipeline.

Everything the rest of the pipeline needs from this subpackage is re-exported here:

- `GeometricRatioFeatureLayer` — the custom Keras layer computing EAR/MAR/pose ratios, ported
  verbatim from `notebook/01_dataset_creation_lstm.ipynb` (source of truth). Used directly (not
  deserialized) by `fused_features.py`'s geometric-feature computation.
- `download_cnn_model` — fetches `07`'s trained CNN checkpoint from Google Drive via `gdown`
  (needed as the frozen embedding backbone, see below), skipping the download if already cached
  in `MODEL_DIR` (see `downloader.py`).
- `download_fused_model` — fetches the trained fused CNN+LSTM checkpoint
  (`best_cnn_lstm_frozen_embedding.keras`) the same way. This is the model the container deploys.
- `CnnDrowsinessDetector` — loads the CNN checkpoint and exposes its penultimate layer as a
  frozen embedding sub-model (`embedding_submodel()`); no longer used for its own single-frame
  classification (see `cnn_detector.py`'s module docstring).
- `FusedDrowsinessDetector` — loads both artifacts, maintains the per-camera-stream sliding
  window buffer, and exposes `predict_frame(face_crop_rgb, geo_features) -> DetectionResult`.
  This is the only detector `pipeline/` calls.
- `compute_fused_geo_features`/`FUSED_GEO_FEATURE_NAMES` (`fused_features.py`) — the 10-feature
  geometric subset `FusedDrowsinessDetector.predict_frame()` expects.
"""

from .layers import GeometricRatioFeatureLayer
from .downloader import download_cnn_model, download_fused_model
from .detector import DetectionResult
from .cnn_detector import CnnDrowsinessDetector
from .fused_detector import FusedDrowsinessDetector
from .fused_features import compute_fused_geo_features, zero_fused_geo_features, FUSED_GEO_FEATURE_NAMES

__all__ = [
    "GeometricRatioFeatureLayer",
    "download_cnn_model",
    "download_fused_model",
    "DetectionResult",
    "CnnDrowsinessDetector",
    "FusedDrowsinessDetector",
    "compute_fused_geo_features",
    "zero_fused_geo_features",
    "FUSED_GEO_FEATURE_NAMES",
]
