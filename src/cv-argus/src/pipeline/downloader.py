"""Fetches the pretrained MediaPipe Face Detector bundle `FaceDetectorCropStage` needs.

A plain, public, unauthenticated HTTP download — no Drive file ID, no sharing settings, no
credentials, unlike `cv_argus.model.downloader`'s trained-model fetches.

- `download_face_detector_bundle()` — the Face Detector (BlazeFace, short_range/float16)
  `.tflite` bundle (bounding-box-only, no landmarks). Same URL/behavior as
  `notebook/06_dataset_creation_face_crops.ipynb`'s "MediaPipe Face Detector Setup" cell and the
  parts dataset builder.

It lives here rather than in `model/` because it's this subpackage's dependency (nothing in
`model/` touches MediaPipe).

URL/filename default from `constants.py` (see its docstring), overridable via the matching
environment variable — an empty-string env var (e.g. an unset Docker build `ARG` substituted in)
is treated the same as an absent one, same convention as `cv_argus.model.downloader`.
"""

import logging
import os
import urllib.request
from pathlib import Path

from .. import constants

logger = logging.getLogger(__name__)


class BundleDownloadError(RuntimeError):
    """Raised when a MediaPipe bundle can't be fetched and no local copy already exists."""


def _download_bundle(url: str, model_dir: Path, filename: str, *, label: str) -> Path:
    """Shared skip-if-cached-download logic for one public, HTTP-hosted MediaPipe bundle.

    `label` is just for log/error messages (e.g. "Face Landmarker bundle", "Face Detector
    bundle") so the two public functions below don't duplicate this logic with no
    distinguishing detail — mirrors `cv_argus.model.downloader`'s `_download_from_drive`.
    """
    destination = model_dir / filename

    if destination.exists():
        logger.info("%s already cached at %s, skipping download.", label, destination)
        return destination

    model_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Downloading %s from %s...", label, url)
    try:
        urllib.request.urlretrieve(url, destination)
    except Exception as exc:
        raise BundleDownloadError(f"Failed to download {label} from {url}: {exc}") from exc

    logger.info("%s downloaded to %s", label, destination)
    return destination


def download_face_detector_bundle(
    model_dir: str | os.PathLike | None = None,
    filename: str | None = None,
    url: str | None = None,
) -> Path:
    """Ensure the pretrained MediaPipe Face Detector (BlazeFace, short_range/float16) `.tflite`
    bundle is present locally.

    Reads `MODEL_DIR` plus `FACE_DETECTOR_BUNDLE_URL`/`FACE_DETECTOR_BUNDLE_FILENAME` from the
    environment for any argument left as `None`, falling back to `constants.py`'s defaults if
    unset. Needed by `FaceDetectorCropStage`.

    Called from the Dockerfile at *build* time (`python -m cv_argus.pipeline.downloader`), so the
    device can start monitoring with no signal in the truck at that moment.

    Returns the local path to the bundle file.
    """
    model_dir = Path(model_dir or os.environ.get("MODEL_DIR") or constants.MODEL_DIR_DEFAULT)
    filename = (
        filename
        or os.environ.get("FACE_DETECTOR_BUNDLE_FILENAME")
        or constants.FACE_DETECTOR_BUNDLE_FILENAME
    )
    url = (
        url
        or os.environ.get("FACE_DETECTOR_BUNDLE_URL")
        or constants.FACE_DETECTOR_BUNDLE_URL
    )
    return _download_bundle(url, model_dir, filename, label="Face Detector bundle")


if __name__ == "__main__":
    # `python -m cv_argus.pipeline.downloader` — called from the Dockerfile at build time.
    logging.basicConfig(level=logging.INFO)
    download_face_detector_bundle()
