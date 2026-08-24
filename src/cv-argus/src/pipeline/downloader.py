"""Fetches pretrained MediaPipe model bundles used by pipeline/'s camera-loop stages.

Two bundles, each a plain, public, unauthenticated HTTP download — no Drive file ID, no
sharing settings, no credentials, unlike `cv_argus.model.downloader`'s trained-model fetches:

- `download_face_landmarker_bundle()` — the Face Landmarker `.task` bundle the (now-optional)
  LSTM path's `FaceLandmarkerStage` needs (478 landmarks + blendshapes + head pose). Same
  URL/behavior as `notebook/01_dataset_creation_lstm.ipynb`'s "Downloading and Setting Up the
  MediaPipe Model Bundle" cell.
- `download_face_detector_bundle()` — the Face Detector (BlazeFace, short_range/float16)
  `.tflite` bundle the CNN path's `FaceDetectorCropStage` needs (bounding-box-only, no
  landmarks). Same URL/behavior as `notebook/06_dataset_creation_face_crops.ipynb`'s "MediaPipe
  Face Detector Setup" cell — a different, lighter model from the Landmarker above, not a typo.

Both live here rather than in `model/` because they're this subpackage's dependency (neither
detector class in `model/` touches MediaPipe) — the stages in `pipeline/` that wrap
`FaceLandmarker`/`FaceDetector` are what actually use these downloaded bundles.

URLs/filenames default from `constants.py` (see its docstring), overridable per-bundle via the
matching environment variable — an empty-string env var (e.g. an unset Docker build `ARG`
substituted in) is treated the same as an absent one, same convention as
`cv_argus.model.downloader`. Neither bundle needs a readiness gate the way the CNN's trained
`.keras` artifact does (see `model/downloader.py`): these are Google's own pretrained,
public-by-default weights, not a project-trained artifact — safe to always fetch at build time
regardless of which model family ends up deployed.
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


def download_face_landmarker_bundle(
    model_dir: str | os.PathLike | None = None,
    filename: str | None = None,
    url: str | None = None,
) -> Path:
    """Ensure the pretrained MediaPipe Face Landmarker `.task` bundle is present locally.

    Reads `MODEL_DIR` (shared with `cv_argus.model.downloader` — one cache directory for all
    startup artifacts) plus `FACE_LANDMARKER_BUNDLE_URL`/`FACE_LANDMARKER_BUNDLE_FILENAME` from
    the environment for any argument left as `None`, falling back to `constants.py`'s defaults
    if unset. Needed only by the (now-optional) LSTM path.

    Called from the Dockerfile at *build* time (see the `RUN python -m
    cv_argus.pipeline.downloader` step there) — baked into the image alongside the trained
    model(s), for the same reason: the device needs to boot and start monitoring the driver
    even with no signal in the truck at that moment, not depend on a runtime download.

    Returns the local path to the bundle file.
    """
    model_dir = Path(model_dir or os.environ.get("MODEL_DIR") or constants.MODEL_DIR_DEFAULT)
    filename = (
        filename
        or os.environ.get("FACE_LANDMARKER_BUNDLE_FILENAME")
        or constants.FACE_LANDMARKER_BUNDLE_FILENAME
    )
    url = (
        url
        or os.environ.get("FACE_LANDMARKER_BUNDLE_URL")
        or constants.FACE_LANDMARKER_BUNDLE_URL
    )
    return _download_bundle(url, model_dir, filename, label="Face Landmarker bundle")


def download_face_detector_bundle(
    model_dir: str | os.PathLike | None = None,
    filename: str | None = None,
    url: str | None = None,
) -> Path:
    """Ensure the pretrained MediaPipe Face Detector (BlazeFace, short_range/float16) `.tflite`
    bundle is present locally.

    Reads `MODEL_DIR` plus `FACE_DETECTOR_BUNDLE_URL`/`FACE_DETECTOR_BUNDLE_FILENAME` from the
    environment for any argument left as `None`, falling back to `constants.py`'s defaults if
    unset. Needed by the CNN path's `FaceDetectorCropStage` — a different, lighter bundle from
    the Landmarker one above (bounding-box-only, no landmarks/blendshapes), matching
    `notebook/06_dataset_creation_face_crops.ipynb`'s "MediaPipe Face Detector Setup" cell.

    Called from the Dockerfile at *build* time, same as the Landmarker bundle above.

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
    download_face_detector_bundle()    # CNN path — the model this container actually deploys
    download_face_landmarker_bundle()  # LSTM path — kept, optional; harmless to fetch either way
