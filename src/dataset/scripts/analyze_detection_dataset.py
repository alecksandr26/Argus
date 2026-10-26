#!/usr/bin/env python3
"""Balance report for the built detection dataset (``processed/parts_det/``): per class, how many
boxes, images and independent groups there are in train / valid / test, the split proportions,
and which source each class comes from. Writes PNG charts and ``balance.csv`` to
``processed/parts_det_report/`` and prints the table. Needs ``pip install -e .[analysis]``.

  python scripts/analyze_detection_dataset.py
  python scripts/analyze_detection_dataset.py --out /tmp/report
"""
import argparse
from pathlib import Path

import argus_dataset.bootstrap  # noqa: F401
import pandas as pd

from argus_dataset import det_report, paths


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=None, help="output folder (default processed/parts_det_report)")
    args = ap.parse_args()

    base = paths.processed_dir() / "parts_det"
    if not (base / "index.csv").exists():
        raise SystemExit(f"{base}/index.csv not found: run scripts/build_detection_dataset.py first")
    out = args.out or paths.processed_dir() / "parts_det_report"
    index, labels = det_report.load(base)
    counts = det_report.count_labels(index, labels)
    table = det_report.summary_table(counts)
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / "balance.csv")
    det_report.per_source(counts).to_csv(out / "boxes_by_source.csv")
    with pd.option_context("display.width", 200, "display.max_columns", 30):
        print(table)
    for p in det_report.plot_report(counts, out):
        print("->", p)
    print("->", out / "balance.csv")


if __name__ == "__main__":
    main()
