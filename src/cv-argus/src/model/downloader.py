"""Fetches trained `.keras` model artifacts from Google Drive via `gdown`.

See `src/cv-argus/CLAUDE.md`'s "Model download strategy" section for why `gdown` + a
public-by-link Drive file was chosen over a Google Cloud service account: zero credential
management (no GCP setup, no secret to mount on the Pi), at the cost of the model file being
link-accessible to anyone who has the ID. Every Drive file this module downloads must be shared
as "Anyone with the link" (Viewer), or `gdown` can't fetch it without extra auth.

This only downloads — it doesn't load a model into memory. `PartsDetector.from_env()`
(`parts_detector.py`) calls these functions and then loads the resulting file.

The MediaPipe Face Detector (BlazeFace) `.tflite` bundle is a separate download — see
`pipeline/downloader.py`. It doesn't live here because it's `pipeline/`'s dependency, not this
module's (nothing in `model/` touches MediaPipe).
"""

import logging
import os
from pathlib import Path

import gdown

from .. import constants

logger = logging.getLogger(__name__)


class ModelDownloadError(RuntimeError):
    """Raised when a model can't be fetched and no usable local copy already exists."""


def _download_from_drive(
    file_id: str | None,
    model_dir: Path,
    filename: str,
    *,
    label: str,
) -> Path:
    """Shared gdown-download-if-not-cached logic for one Drive-hosted model artifact.

    `label` is just for log/error messages (e.g. "CNN model", "fused CNN+LSTM model") so the two
    public functions below don't duplicate this logic with no distinguishing detail.
    """
    destination = model_dir / filename

    if destination.exists():
        logger.info("%s already cached at %s, skipping download.", label, destination)
        return destination

    if not file_id:
        raise ModelDownloadError(
            f"No {label} found at {destination} and no Drive file id is set — can't download "
            "it. See constants.py and the matching environment variable."
        )

    model_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Downloading %s (file id %s) to %s...", label, file_id, destination)
    try:
        result = gdown.download(id=file_id, output=str(destination), quiet=False)
    except Exception as exc:  # gdown raises a range of exceptions depending on the failure
        raise ModelDownloadError(
            f"Failed to download {label} from Google Drive (file id {file_id}): {exc}"
        ) from exc

    if result is None or not destination.exists():
        raise ModelDownloadError(
            f"gdown reported no error but {destination} was not created — check that the "
            f"Drive file id {file_id!r} is correct and shared as 'Anyone with the link'."
        )

    logger.info("%s downloaded to %s", label, destination)
    return destination


def _download_parts(kind: str, label: str, file_id, model_dir, filename) -> Path:
    file_id = file_id or os.environ.get(f"PARTS_{kind}_DRIVE_FILE_ID") or getattr(constants, f"PARTS_{kind}_DRIVE_FILE_ID")
    model_dir = Path(model_dir or os.environ.get("MODEL_DIR") or constants.MODEL_DIR_DEFAULT)
    filename = filename or os.environ.get(f"PARTS_{kind}_FILENAME") or getattr(constants, f"PARTS_{kind}_FILENAME")
    return _download_from_drive(file_id, model_dir, filename, label=label)


def download_parts_int8_model(file_id=None, model_dir=None, filename=None) -> Path:
    """The "parts" detector (notebook 12's `tiny_yolo_w1`) as full-integer int8 TFLite, the
    default `PARTS_PRECISION`. Checked-in Drive id `constants.PARTS_INT8_DRIVE_FILE_ID`,
    overridable by the env var of the same name."""
    return _download_parts("INT8", "parts detector (int8 tflite)", file_id, model_dir, filename)


def download_parts_float_model(file_id=None, model_dir=None, filename=None) -> Path:
    """The same detector as the float `.keras` (`PARTS_PRECISION=float`), kept to check whether
    int8 quantisation costs accuracy."""
    return _download_parts("FLOAT", "parts detector (float keras)", file_id, model_dir, filename)


if __name__ == "__main__":
    # `python -m cv_argus.model.downloader` — called from the Dockerfile at build time. Both
    # precisions of the parts detector are baked in, so switching PARTS_PRECISION never needs a
    # network at container start. Either download failing fails the image build.
    logging.basicConfig(level=logging.INFO)
    download_parts_int8_model()
    download_parts_float_model()
