#!/usr/bin/env python3
"""src/notebook/01_dataset_creation_lstm.ipynb -> ``processed/lstm_windows.csv``.

FaceLandmarker per-frame geometric features (7 ratios + 51 blendshapes), sliding windows of
1-6 s at 1 s stride, windows with any invalid-pose frame dropped, survivors zero-pre-padded to
30 timesteps and flattened. Pausable/resumable: Ctrl-C to pause, re-run the same command to
resume; ``--status`` / ``--reset``.
"""
import argparse

import argus_dataset.bootstrap  # noqa: F401
from argus_dataset import assets, cli, config, paths, pipelines, workers


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_common_args(ap)
    args = ap.parse_args()

    if not (args.status or args.dry_run):
        assets.ensure_face_landmarker()
    pipeline = pipelines.FaceLandmarkerFeatureExtractor(paths.face_landmarker_path())

    raise SystemExit(workers.run_video_build(
        "lstm_windows", paths.lstm_windows_csv(), config.lstm_csv_columns(), pipeline,
        workers=args.workers, subjects=cli.subjects_list(args), limit=args.limit,
        dry_run=args.dry_run, reset=args.reset, force=args.force, status=args.status,
    ))


if __name__ == "__main__":
    main()
