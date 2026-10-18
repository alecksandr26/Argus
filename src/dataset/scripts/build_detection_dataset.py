#!/usr/bin/env python3
"""Build the YOLO-format parts detection dataset (face crops; classes open_eye, closed_eye, yawn,
head_straight, head_down) under ``<ARGUS_DATASET_ROOT>/processed/parts_det/``, the single dataset
for notebooks 12/13/14. Needs the raw pulls first (fetch_mrl_eye.py, fetch_roboflow_parts.py) and
the MediaPipe models (fetch_models.py).

  python scripts/build_detection_dataset.py --mediapipe-only   # slow pass only (resumable)
  python scripts/build_detection_dataset.py --sheet            # build + contact sheet (pitch check)
  python scripts/build_detection_dataset.py --zip              # build + processed/parts_det.zip
"""
import argparse
import json
import os

import argus_dataset.bootstrap  # noqa: F401
from tqdm import tqdm

from argus_dataset import detection_dataset as dd
from argus_dataset import external


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--mediapipe-only", action="store_true", help="run/resume the MediaPipe pass, then stop")
    ap.add_argument("--sheet", action="store_true", help="write preview_faces.jpg (boxes + pitch)")
    ap.add_argument("--zip", action="store_true", help="package processed/parts_det.zip")
    args = ap.parse_args()

    images = dd.roboflow_images(external.load_manifest())
    cache = dd.run_mediapipe_pass(images, args.workers, progress=lambda it, **k: tqdm(it, **k))
    print(f"MediaPipe cache: {len(cache)} images, "
          f"{sum(r['face_found'] == '1' for r in cache.values())} faces, "
          f"{sum(r['landmarks_found'] == '1' for r in cache.values())} with pose")
    if args.mediapipe_only:
        return
    print(json.dumps(dd.build(cache, progress=lambda it, **k: tqdm(it, **k)), indent=2))
    if args.sheet:
        print(f"-> {dd.contact_sheet()}")
    if args.zip:
        z = dd.export_zip()
        print(f"-> {z}  ({z.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
