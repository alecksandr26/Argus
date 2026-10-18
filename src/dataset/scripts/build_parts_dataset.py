#!/usr/bin/env python3
"""Merge MRL Eye + the Roboflow projects into one 3-class dataset (open_eye / closed_eye / yawn)
under ``<ARGUS_DATASET_ROOT>/processed/parts_dataset/``, with an IR-simulated copy of every RGB
crop. Writes ``parts_dataset.csv`` (one row per crop, every source) and ``class_map.csv`` (how
each source label was merged or dropped). Needs the raw pulls first (fetch_mrl_eye.py,
fetch_roboflow_parts.py).

  python scripts/build_parts_dataset.py              # build (reuses existing crop files)
  python scripts/build_parts_dataset.py --preview    # also write rgb-vs-ir contact sheets per class
  python scripts/build_parts_dataset.py --zip        # also package processed/parts_dataset.zip
"""
import argparse
import json

import argus_dataset.bootstrap  # noqa: F401
from tqdm import tqdm

from argus_dataset import parts_dataset, paths


def preview(n_per_class: int = 12) -> None:
    import cv2
    import numpy as np
    import pandas as pd
    df = pd.read_csv(paths.parts_dataset_csv())
    df = df[df["source"] == "roboflow"]
    root, out = paths.dataset_root(), paths.parts_dataset_dir() / "preview"
    out.mkdir(exist_ok=True)
    for cls, g in df.groupby("class"):
        tiles = []
        for _, r in g.sample(min(n_per_class, len(g)), random_state=0).iterrows():
            rgb = cv2.resize(cv2.imread(str(root / r["rgb_path"])), (96, 96))
            irg = cv2.resize(cv2.imread(str(root / r["ir_path"])), (96, 96))
            tiles.append(np.vstack([rgb, irg]))
        cv2.imwrite(str(out / f"{cls}.jpg"), np.hstack(tiles))
    print(f"preview sheets (top row RGB, bottom row IR) -> {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--zip", action="store_true", help="package the result as a self-contained zip")
    args = ap.parse_args()
    summary = parts_dataset.build(progress=lambda it, **k: tqdm(it, **k))
    print(json.dumps(summary, indent=2))
    print(f"-> {paths.parts_dataset_csv()}\n-> {paths.parts_class_map_csv()}")
    if args.preview:
        preview()
    if args.zip:
        z = parts_dataset.export_zip()
        print(f"-> {z}  ({z.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
