"""Fetches trained `.keras` model artifacts from Google Drive via `gdown`.

See `src/cv-argus/CLAUDE.md`'s "Model download strategy" section for why `gdown` + a
public-by-link Drive file was chosen over a Google Cloud service account: zero credential
management (no GCP setup, no secret to mount on the Pi), at the cost of the model file being
link-accessible to anyone who has the ID. Every Drive file this module downloads must be shared
as "Anyone with the link" (Viewer), or `gdown` can't fetch it without extra auth.

This only downloads — it doesn't load a model into memory or know anything about Keras.
`CnnDrowsinessDetector`/`FusedDrowsinessDetector` (`cnn_detector.py`/`fused_detector.py`) are
what call these functions and then load the resulting file.

The MediaPipe Face Landmarker/Face Detector `.task`/`.tflite` bundles are a separate, unrelated
download — see `pipeline/downloader.py` for those. They don't live here because they aren't this
module's dependency (neither class here touches MediaPipe); they're `pipeline/`'s.
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


def download_cnn_model(
    file_id: str | None = None,
    model_dir: str | os.PathLike | None = None,
    filename: str | None = None,
) -> Path:
    """Ensure the trained CNN face-crop checkpoint is present locally, downloading if needed.

    Required, not for its own classification (see `cnn_detector.py`'s module docstring), but as
    the frozen embedding backbone `download_fused_model()`'s model depends on.
    `constants.CNN_MODEL_DRIVE_FILE_ID` gives this a checked-in default, so this succeeds with no
    environment configuration at all; the `CNN_MODEL_DRIVE_FILE_ID` env var still overrides it,
    for swapping in a newer trained model without a code change. An env var
    present-but-set-to-empty-string (e.g. an unset Docker build `ARG` substituted in) is treated
    the same as absent, falling through to the `constants.py` default either way.

    Called from the Dockerfile at *build* time (`python -m cv_argus.model.downloader`) — baked
    into the image rather than fetched at container start, so the device can boot and start
    monitoring the driver even if the truck has no signal at that exact moment.

    Returns the local path to the model file.
    """
    file_id = (
        file_id
        or os.environ.get("CNN_MODEL_DRIVE_FILE_ID")
        or constants.CNN_MODEL_DRIVE_FILE_ID
    )
    model_dir = Path(
        model_dir or os.environ.get("MODEL_DIR") or constants.MODEL_DIR_DEFAULT
    )
    filename = (
        filename or os.environ.get("CNN_MODEL_FILENAME") or constants.CNN_MODEL_FILENAME
    )
    return _download_from_drive(file_id, model_dir, filename, label="CNN model")


def download_fused_model(
    file_id: str | None = None,
    model_dir: str | os.PathLike | None = None,
    filename: str | None = None,
) -> Path:
    """Ensure the trained fused CNN-embedding + geometric-feature + LSTM classifier
    (`best_cnn_lstm_frozen_embedding.keras`, `notebook/11_cnn_lstm_training_drive_pull.ipynb`)
    is present locally, downloading if needed. Required — this is the model this container
    deploys.

    `constants.FUSED_MODEL_DRIVE_FILE_ID` gives this a checked-in default (same "Anyone with the
    link isn't a secret" rationale `CNN_MODEL_DRIVE_FILE_ID` is checked in under), so this
    succeeds with no environment configuration; `FUSED_MODEL_DRIVE_FILE_ID` still overrides it.

    Returns the local path to the model file.
    """
    file_id = (
        file_id
        or os.environ.get("FUSED_MODEL_DRIVE_FILE_ID")
        or constants.FUSED_MODEL_DRIVE_FILE_ID
    )
    model_dir = Path(
        model_dir or os.environ.get("MODEL_DIR") or constants.MODEL_DIR_DEFAULT
    )
    filename = (
        filename or os.environ.get("FUSED_MODEL_FILENAME") or constants.FUSED_MODEL_FILENAME
    )
    return _download_from_drive(file_id, model_dir, filename, label="fused CNN+LSTM model")


if __name__ == "__main__":
    # `python -m cv_argus.model.downloader` — called from the Dockerfile at build time. Both
    # downloads are required: the fused model is what this container deploys, and it needs the
    # CNN checkpoint as its frozen embedding backbone (see fused_detector.py). Either one
    # failing should fail the whole image build, not silently produce an image that can't start.
    logging.basicConfig(level=logging.INFO)
    download_cnn_model()
    download_fused_model()
