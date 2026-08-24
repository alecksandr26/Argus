"""Model loading and inference for the cv-argus edge pipeline.

Everything the rest of the pipeline needs from this subpackage is re-exported here:

- `GeometricRatioFeatureLayer`, `LstmGeometricFeatureModel` — the custom Keras classes for the
  LSTM path, ported verbatim from `notebook/01_dataset_creation_lstm.ipynb` (source of truth for
  `GeometricRatioFeatureLayer`) and `notebook/08_deployment_export_lstm.ipynb`
  (`LstmGeometricFeatureModel`). They must stay importable under these exact names: loading a
  saved model that uses custom Keras classes requires passing the same class objects back in as
  `custom_objects`, since Keras can't reconstruct arbitrary `call()` logic from the saved file
  alone.
- `download_model` (LSTM, kept as a backwards-compatible alias for `download_lstm_model`) /
  `download_cnn_model` — fetch trained `.keras` artifacts from Google Drive via `gdown`,
  skipping the download if already cached in `MODEL_DIR` (see `downloader.py`).
- `DrowsinessDetector` (LSTM) / `CnnDrowsinessDetector` (CNN) — load a downloaded artifact and
  wrap it in a `predict_frame(...)`/`predict_crop(...)` call, so `pipeline/` doesn't need to
  know anything about Keras or either model's internals. The CNN is the model this container
  actually deploys right now — see `src/cv-argus/CLAUDE.md`'s "Current status" for why both
  still exist side by side.
"""

from .layers import GeometricRatioFeatureLayer
from .lstm_model import LstmGeometricFeatureModel
from .downloader import download_model, download_lstm_model, download_cnn_model
from .detector import DrowsinessDetector, DetectionResult
from .cnn_detector import CnnDrowsinessDetector

__all__ = [
    "GeometricRatioFeatureLayer",
    "LstmGeometricFeatureModel",
    "download_model",
    "download_lstm_model",
    "download_cnn_model",
    "DrowsinessDetector",
    "DetectionResult",
    "CnnDrowsinessDetector",
]
