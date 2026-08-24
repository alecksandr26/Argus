"""Fetches trained `.keras` model artifacts from Google Drive via `gdown`.

See `src/cv-argus/CLAUDE.md`'s "Model download strategy" section for why `gdown` + a
public-by-link Drive file was chosen over a Google Cloud service account: zero credential
management (no GCP setup, no secret to mount on the Pi), at the cost of the model file being
link-accessible to anyone who has the ID. Every Drive file this module downloads must be shared
as "Anyone with the link" (Viewer), or `gdown` can't fetch it without extra auth.

This only downloads — it doesn't load a model into memory or know anything about Keras.
`CnnDrowsinessDetector`/`DrowsinessDetector` (`cnn_detector.py`/`detector.py`) are what call
these functions and then load the resulting file.

The MediaPipe Face Landmarker `.task` bundle is a separate, unrelated download — see
`pipeline/downloader.py` for that one. It doesn't live here because it isn't this module's
dependency (neither detector class touches it); it's `pipeline/`'s.
"""

import logging
import os
from pathlib import Path

import gdown

from .. import constants

logger = logging.getLogger(__name__)

# Backwards-compatible alias — kept in case anything outside this file still imports it by name.
DEFAULT_MODEL_FILENAME = constants.LSTM_MODEL_FILENAME


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

    `label` is just for log/error messages (e.g. "CNN model", "LSTM model") so `download_cnn_model`
    and `download_lstm_model` below don't duplicate this logic with no distinguishing detail.
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
    """Ensure the trained CNN face-crop classifier is present locally, downloading if needed.

    This is the model the container actually deploys (see `src/cv-argus/CLAUDE.md`'s "Current
    status"). `constants.CNN_MODEL_DRIVE_FILE_ID` gives this a checked-in default, so this
    succeeds with no environment configuration at all; the `CNN_MODEL_DRIVE_FILE_ID` env var
    still overrides it, for swapping in a newer trained model without a code change. An env var
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


def download_lstm_model(
    file_id: str | None = None,
    model_dir: str | os.PathLike | None = None,
    filename: str | None = None,
) -> Path:
    """Ensure the trained LSTM/geometric-feature model is present locally, downloading if needed.

    Still fully functional, but no longer required at build time now that the CNN is the model
    this container actually deploys (see `src/cv-argus/CLAUDE.md`). `constants.py` has no
    checked-in default for `LSTM_MODEL_DRIVE_FILE_ID` (it's `None`), so this only downloads
    anything if `MODEL_DRIVE_FILE_ID` is set explicitly in the environment — the Dockerfile
    treats a `ModelDownloadError` from this call as non-fatal rather than failing the whole
    image build (see its build step).

    Returns the local path to the model file.
    """
    file_id = (
        file_id
        or os.environ.get("MODEL_DRIVE_FILE_ID")
        or constants.LSTM_MODEL_DRIVE_FILE_ID
    )
    model_dir = Path(
        model_dir or os.environ.get("MODEL_DIR") or constants.MODEL_DIR_DEFAULT
    )
    filename = (
        filename or os.environ.get("MODEL_FILENAME") or constants.LSTM_MODEL_FILENAME
    )
    return _download_from_drive(file_id, model_dir, filename, label="LSTM model")


# Backwards-compatible alias: detector.py and model/__init__.py import this name.
download_model = download_lstm_model


if __name__ == "__main__":
    # `python -m cv_argus.model.downloader` — called from the Dockerfile at build time.
    logging.basicConfig(level=logging.INFO)
    download_cnn_model()  # required — has a checked-in default Drive id, so this should succeed
    try:
        download_lstm_model()
    except ModelDownloadError as exc:
        # Optional/best-effort: no checked-in default Drive id for this model anymore, since the
        # CNN is what's actually deployed. Not required for the CNN path to work — log and move
        # on rather than failing the whole build.
        logger.info(
            "Skipping LSTM model download (optional, no MODEL_DRIVE_FILE_ID set): %s", exc
        )
