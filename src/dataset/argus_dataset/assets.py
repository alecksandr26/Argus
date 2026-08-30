"""Download the MediaPipe model bundles the pipeline needs.

Idempotent, same as the notebooks' ``urllib.request.urlretrieve`` skip-if-exists pattern
(src/notebook/01 cell 45, 06 cell 5):

  * ``face_landmarker.task``       — 478 landmarks + 52 blendshapes + transform matrix (01/02/09)
  * ``blaze_face_short_range.tflite`` — lightweight face bounding-box detector (06)
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

from . import config, paths


def _download(url: str, dest: Path) -> bool:
    """Returns True if a download happened, False if the file was already present."""
    if dest.exists() and dest.stat().st_size > 0:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"Downloading {url}\n        -> {dest}")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(dest)
    return True


def ensure_face_landmarker() -> Path:
    dest = paths.face_landmarker_path()
    _download(config.FACE_LANDMARKER_URL, dest)
    return dest


def ensure_face_detector() -> Path:
    dest = paths.face_detector_path()
    _download(config.FACE_DETECTOR_URL, dest)
    return dest


def download_all() -> None:
    landmarker = ensure_face_landmarker()
    detector = ensure_face_detector()
    print(f"OK  {landmarker}  ({landmarker.stat().st_size / 1e6:.1f} MB)")
    print(f"OK  {detector}  ({detector.stat().st_size / 1e6:.1f} MB)")
