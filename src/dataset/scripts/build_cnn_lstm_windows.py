#!/usr/bin/env python3
"""src/notebook/09_dataset_creation_cnn_lstm.ipynb -> ``processed/cnn_lstm_windows_index.csv``.

Reads ``face_crops_index.csv`` (from build_face_crops.py). Step 1: one FaceLandmarker IMAGE
inference per unique crop -> a 10-feature geometric vector, cached and resumable. Step 2:
windows of 3/5/10/20 s tiled per contiguous ``sample_idx`` run -- non-overlapping for
``level_1`` (Not Drowsy), overlap-tiled for ``level_2`` (Drowsy) per
``config.CNNLSTM_MINORITY_WINDOW_OVERLAP`` (minority-class rebalancing; see src/dataset/CLAUDE.md).

Step 2 is pure and cheap, so it ALWAYS re-runs over the full geometry cache and rewrites
``cnn_lstm_windows_index.csv``. To pick up a change to the window configs / overlap, just re-run
this script -- no ``--reset`` needed (``--reset`` throws away the geometry cache and forces the
slow step-1 re-extraction).
"""
import argparse

import argus_dataset.bootstrap  # noqa: F401
from argus_dataset import assets, cnn_lstm


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not (args.status or args.dry_run):
        assets.ensure_face_landmarker()

    raise SystemExit(cnn_lstm.run(
        workers_n=args.workers, reset=args.reset, status=args.status,
        force=args.force, dry_run=args.dry_run,
    ))


if __name__ == "__main__":
    main()
