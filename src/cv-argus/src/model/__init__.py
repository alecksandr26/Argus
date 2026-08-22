"""Model loading and inference for the cv-argus edge pipeline.

Everything the rest of the pipeline needs from this subpackage is re-exported here:

- `GeometricRatioFeatureLayer`, `LstmGeometricFeatureModel` — the custom Keras classes,
  ported verbatim from `notebook/01_dataset_creation.ipynb` (source of truth for
  `GeometricRatioFeatureLayer`, also redefined byte-identical in `03_deployment_export.ipynb`)
  and `notebook/03_deployment_export.ipynb` (`LstmGeometricFeatureModel`). They must stay
  importable under these exact names: loading a saved model that uses custom Keras classes
  requires passing the same class objects back in as `custom_objects`, since Keras can't
  reconstruct arbitrary `call()` logic from the saved file alone.
- `download_model` — fetches the trained `.keras` artifact from Google Drive via `gdown`,
  skipping the download if it's already cached in `MODEL_DIR`.
- `DrowsinessDetector` — loads the downloaded artifact and wraps it in a `predict_frame(...)`
  call that takes one frame's raw MediaPipe outputs and returns a `(level, probabilities)`
  pair, so `pipeline/` doesn't need to know anything about Keras or the model's internal
  buffering.
"""

from .layers import GeometricRatioFeatureLayer
from .lstm_model import LstmGeometricFeatureModel
from .downloader import download_model
from .detector import DrowsinessDetector

__all__ = [
    "GeometricRatioFeatureLayer",
    "LstmGeometricFeatureModel",
    "download_model",
    "DrowsinessDetector",
]
