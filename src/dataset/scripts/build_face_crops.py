#!/usr/bin/env python3
"""src/notebook/06_dataset_creation_face_crops.ipynb -> ``processed/face_crops/*.jpg`` +
``processed/face_crops_index.csv``.

BlazeFace bounding box per sampled frame, 25% margin, native-resolution JPEG crops, capped at
100 crops/clip. Pausable/resumable. ``--reset`` also wipes ``processed/face_crops/``.
"""
import argparse

import argus_dataset.bootstrap  # noqa: F401
from argus_dataset import assets, cli, config, paths, pipelines, workers


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_common_args(ap)
    args = ap.parse_args()

    if not (args.status or args.dry_run):
        assets.ensure_face_detector()
    pipeline = pipelines.FaceCropExtractor(paths.face_detector_path())

    raise SystemExit(workers.run_video_build(
        "face_crops", paths.face_crops_index_csv(), config.FACE_CROPS_INDEX_COLS, pipeline,
        workers=args.workers, subjects=cli.subjects_list(args), limit=args.limit,
        dry_run=args.dry_run, reset=args.reset, force=args.force, status=args.status,
        extra_reset_paths=(paths.face_crops_dir(),),
    ))


if __name__ == "__main__":
    main()
