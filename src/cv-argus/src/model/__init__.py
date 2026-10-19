"""Model loading and inference for the cv-argus edge pipeline: the "parts" detector.

Everything the rest of the pipeline needs from this subpackage is re-exported here:

- `download_parts_int8_model` / `download_parts_float_model` (`downloader.py`): fetch notebook
  12's tiny YOLO (`tiny_yolo_w1`) from Google Drive via `gdown`, as int8 `.tflite` (default) or
  float `.keras`, skipping the download if already cached in `MODEL_DIR`.
- `DetectionResult` (`detector.py`): the per-frame verdict the orchestrator consumes.

The detector itself (`parts_detector.py`), the crop/box geometry (`parts_geometry.py`) and the
tracker (`parts_tracker.py`) are imported from their own modules; `parts_detector` pulls in
TensorFlow lazily, so importing this package stays cheap.

The CNN+LSTM ("fused") pipeline that used to live here is preserved on branch
`archive/cnn-lstm-fused`.
"""

from .downloader import download_parts_float_model, download_parts_int8_model
from .detector import DetectionResult

__all__ = [
    "download_parts_int8_model",
    "download_parts_float_model",
    "DetectionResult",
]
