"""Fetches the trained `.keras` model artifact from Google Drive via `gdown`.

See the root README's "Model download strategy" section for why `gdown` + a public-by-link
Drive file was chosen over a Google Cloud service account: zero credential management (no GCP
setup, no secret to mount on the Pi), at the cost of the model file being link-accessible to
anyone who has the ID. The Drive file must be shared as "Anyone with the link" (Viewer) or
`gdown` can't fetch it without extra auth.

This only downloads — it doesn't load the model into memory or know anything about Keras.
`DrowsinessDetector` (`detector.py`) is what calls this and then loads the resulting file.

The MediaPipe Face Landmarker `.task` bundle is a separate, unrelated download — see
`pipeline/downloader.py` for that one. It doesn't live here because it isn't this module's
dependency (`detector.py` never touches it); it's `pipeline/`'s.
"""

import logging
import os
from pathlib import Path

import gdown

logger = logging.getLogger(__name__)

DEFAULT_MODEL_FILENAME = "lstm_geometric_feature_model.keras"


class ModelDownloadError(RuntimeError):
    """Raised when the model can't be fetched and no usable local copy already exists."""


def download_model(
    file_id: str | None = None,
    model_dir: str | os.PathLike | None = None,
    filename: str | None = None,
) -> Path:
    """Ensure the trained model artifact is present locally, downloading it if needed.

    Reads `MODEL_DRIVE_FILE_ID`, `MODEL_DIR`, and `MODEL_FILENAME` from the environment
    (see `.env.example`) for any argument left as `None`, matching how the rest of the
    container is configured. Skips the download if the destination file already exists.

    Called from the Dockerfile at *build* time (see the `MODEL_DRIVE_FILE_ID` build `ARG` and
    the `RUN python -m cv_argus.model.downloader` step there) — this artifact is baked into
    the image rather than fetched at container start, so the device can boot and start
    monitoring the driver even if the truck has no signal at that exact moment. The accepted
    trade-off: a new trained model needs an image rebuild + redeploy, not just a container
    restart, and `docker build` itself now needs network access and a valid
    `MODEL_DRIVE_FILE_ID`.

    Returns the local path to the model file.
    """
    file_id = file_id or os.environ.get("MODEL_DRIVE_FILE_ID")
    model_dir = Path(model_dir or os.environ.get("MODEL_DIR", "/app/models"))
    filename = filename or os.environ.get("MODEL_FILENAME", DEFAULT_MODEL_FILENAME)

    destination = model_dir / filename

    if destination.exists():
        logger.info("Model already cached at %s, skipping download.", destination)
        return destination

    if not file_id:
        raise ModelDownloadError(
            f"No model found at {destination} and MODEL_DRIVE_FILE_ID is not set — "
            "can't download it. Set MODEL_DRIVE_FILE_ID in .env to the trained model's "
            "Google Drive file ID (shared as 'Anyone with the link')."
        )

    model_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Downloading model %s from Google Drive (file id %s)...", filename, file_id)
    try:
        result = gdown.download(id=file_id, output=str(destination), quiet=False)
    except Exception as exc:  # gdown raises a range of exceptions depending on the failure
        raise ModelDownloadError(
            f"Failed to download model from Google Drive (file id {file_id}): {exc}"
        ) from exc

    if result is None or not destination.exists():
        raise ModelDownloadError(
            f"gdown reported no error but {destination} was not created — check that the "
            f"Drive file id {file_id!r} is correct and shared as 'Anyone with the link'."
        )

    logger.info("Model downloaded to %s", destination)
    return destination


if __name__ == "__main__":
    # `python -m cv_argus.model.downloader` — called from the Dockerfile at build time.
    logging.basicConfig(level=logging.INFO)
    download_model()
