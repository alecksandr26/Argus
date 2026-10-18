"""Filesystem layout for the local pipeline.

The data root is ``$ARGUS_DATASET_ROOT`` (env var), defaulting to the ``src/dataset/`` directory
this package lives in. Everything mirrors the Google Drive layout one-to-one so that
``publish_to_drive.py`` is a straight prefix swap:

    <root>/
      raw/
        raw_videos/          subject_NN/level_<1-2>_clip_NN.mp4   (level_1 = Not Drowsy, level_2 = Drowsy)
      processed/
        lstm_windows.csv  frame_features.csv  frame_features_enriched.csv
        face_crops_index.csv  cnn_lstm_windows_index.csv
        face_crops/*.jpg
        .progress/  .cache/
      models/
        face_landmarker.task  blaze_face_short_range.tflite
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import config

_ENV_VAR = "ARGUS_DATASET_ROOT"


def dataset_root() -> Path:
    """Resolve the data root. Env var wins; otherwise the ``src/dataset/`` package dir."""
    env = os.environ.get(_ENV_VAR)
    root = Path(env).expanduser().resolve() if env else Path(__file__).resolve().parent.parent
    _warn_if_on_windows_mount(root)
    return root


def _warn_if_on_windows_mount(root: Path) -> None:
    """A data root under /mnt/c (the Windows drive) makes the ~100k-file face_crops/ write
    an order of magnitude slower and can corrupt hardlinks. Warn loudly, once."""
    if str(root).startswith("/mnt/") and not os.environ.get("_ARGUS_MNT_WARNED"):
        os.environ["_ARGUS_MNT_WARNED"] = "1"
        print(
            f"\n  WARNING: ARGUS_DATASET_ROOT resolves to {root}, which is on a Windows-mounted\n"
            "  drive. Small-file I/O there is very slow and hardlinks are unreliable. Move the\n"
            "  data root onto the WSL2 ext4 filesystem (e.g. unset ARGUS_DATASET_ROOT to use\n"
            f"  the default, {Path(__file__).resolve().parent.parent}).\n",
            file=sys.stderr,
        )


# --- directories -----------------------------------------------------------------------------

def raw_dir() -> Path:
    """The input tree the builds read: subject_NN/level_<1-2>_clip_NN.mp4, already labelled
    binary (level_1 = Not Drowsy, level_2 = Drowsy)."""
    return dataset_root() / "raw" / "raw_videos"


def collection_log_csv() -> Path:
    """Append-only provenance log written by ``scripts/collect_clips.py`` — one row per clip
    filed into ``raw_dir()``. Not consumed by the builds; safe to read or delete."""
    return raw_dir() / "collection_log.csv"


def processed_dir() -> Path:
    return dataset_root() / "processed"


def face_crops_dir() -> Path:
    return processed_dir() / "face_crops"


def models_dir() -> Path:
    return dataset_root() / "models"


def progress_dir() -> Path:
    return processed_dir() / ".progress"


def cache_dir() -> Path:
    return processed_dir() / ".cache"


def ensure_dirs() -> None:
    for d in (raw_dir(), processed_dir(), models_dir(), progress_dir(), cache_dir()):
        d.mkdir(parents=True, exist_ok=True)


# --- canonical output files ----------------------------------------------------------------

def lstm_windows_csv() -> Path:
    return processed_dir() / "lstm_windows.csv"


def frame_features_csv() -> Path:
    return processed_dir() / "frame_features.csv"


def frame_features_enriched_csv() -> Path:
    return processed_dir() / "frame_features_enriched.csv"


def face_crops_index_csv() -> Path:
    return processed_dir() / "face_crops_index.csv"


def cnn_lstm_windows_index_csv() -> Path:
    return processed_dir() / "cnn_lstm_windows_index.csv"


def geo_per_crop_cache() -> Path:
    """Incremental per-crop geometry cache for build_cnn_lstm_windows.py (parquet, CSV fallback)."""
    return cache_dir() / "geo_per_crop.parquet"


# --- external datasets (parts architecture) ---------------------------------------------------

def external_raw_dir() -> Path:
    """``raw/`` itself — the external datasets sit beside ``raw_videos/``, not inside it."""
    return dataset_root() / "raw"


def datasets_manifest_json() -> Path:
    return external_raw_dir() / "datasets_manifest.json"


def mrl_eye_raw_dir() -> Path:
    return external_raw_dir() / "mrl_eye"


def roboflow_raw_dir() -> Path:
    return external_raw_dir() / "roboflow"


def parts_dataset_dir() -> Path:
    """Merged 3-class parts dataset: ``{rgb,ir}/{open_eye,closed_eye,yawn}/`` + CSVs."""
    return processed_dir() / "parts_dataset"


def parts_dataset_csv() -> Path:
    return parts_dataset_dir() / "parts_dataset.csv"


def parts_class_map_csv() -> Path:
    return parts_dataset_dir() / "class_map.csv"


def ir_reference_hist() -> Path:
    return parts_dataset_dir() / "ir_ref_hist.npy"


# --- model bundles -------------------------------------------------------------------------

def face_landmarker_path() -> Path:
    return models_dir() / config.FACE_LANDMARKER_FILENAME


def face_detector_path() -> Path:
    return models_dir() / config.FACE_DETECTOR_FILENAME
