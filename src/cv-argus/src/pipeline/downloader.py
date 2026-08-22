"""Fetches the pretrained MediaPipe Face Landmarker `.task` bundle.

Same URL and behavior as `notebook/01_dataset_creation.ipynb`'s "Downloading and Setting Up
the MediaPipe Model Bundle" cell: a plain, public, unauthenticated HTTP download — no Drive
file ID, no sharing settings, no credentials, unlike
`cv_argus.model.downloader.download_model()`'s trained-model fetch. Lives here rather than in
`model/` because it's this subpackage's dependency (`detector.py` in `model/` never touches
it) — whatever eventually wraps MediaPipe's `FaceLandmarker` for the live camera loop is what
actually uses the downloaded bundle.
"""

import logging
import os
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

# Same URL 01_dataset_creation.ipynb downloads from. Google's public, unauthenticated model store.
DEFAULT_FACE_LANDMARKER_BUNDLE_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/"
    "float16/latest/face_landmarker.task"
)
DEFAULT_FACE_LANDMARKER_BUNDLE_FILENAME = "face_landmarker.task"


class BundleDownloadError(RuntimeError):
    """Raised when the Face Landmarker bundle can't be fetched and no local copy exists."""


def download_face_landmarker_bundle(
    model_dir: str | os.PathLike | None = None,
    filename: str | None = None,
    url: str | None = None,
) -> Path:
    """Ensure the pretrained MediaPipe Face Landmarker `.task` bundle is present locally.

    Reads `MODEL_DIR` (shared with `cv_argus.model.downloader` — one cache directory for all
    startup artifacts) plus `FACE_LANDMARKER_BUNDLE_URL`/`FACE_LANDMARKER_BUNDLE_FILENAME`
    from the environment for any argument left as `None`. Skips the download if the
    destination file already exists.

    Called from the Dockerfile at *build* time (see the `RUN python -m
    cv_argus.pipeline.downloader` step there) — baked into the image alongside the trained
    LSTM model, for the same reason: the device needs to boot and start monitoring the driver
    even with no signal in the truck at that moment, not depend on a runtime download.

    Returns the local path to the bundle file.
    """
    model_dir = Path(model_dir or os.environ.get("MODEL_DIR", "/app/models"))
    filename = filename or os.environ.get(
        "FACE_LANDMARKER_BUNDLE_FILENAME", DEFAULT_FACE_LANDMARKER_BUNDLE_FILENAME
    )
    url = url or os.environ.get("FACE_LANDMARKER_BUNDLE_URL", DEFAULT_FACE_LANDMARKER_BUNDLE_URL)

    destination = model_dir / filename

    if destination.exists():
        logger.info("Face Landmarker bundle already cached at %s, skipping download.", destination)
        return destination

    model_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Downloading Face Landmarker bundle from %s...", url)
    try:
        urllib.request.urlretrieve(url, destination)
    except Exception as exc:
        raise BundleDownloadError(
            f"Failed to download the Face Landmarker bundle from {url}: {exc}"
        ) from exc

    logger.info("Face Landmarker bundle downloaded to %s", destination)
    return destination


if __name__ == "__main__":
    # `python -m cv_argus.pipeline.downloader` — called from the Dockerfile at build time.
    logging.basicConfig(level=logging.INFO)
    download_face_landmarker_bundle()
