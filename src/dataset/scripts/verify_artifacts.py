#!/usr/bin/env python3
"""Check the generated artifacts against the schema/relationship contract the training
notebooks rely on. Exit code 0 iff every check for every existing artifact passes.

``--compare REF.csv --artifact {lstm_windows,frame_features}`` additionally diffs against a
Colab-produced CSV (column parity + per-level counts + per-feature mean/std tolerance).
"""
import argparse

import argus_dataset.bootstrap  # noqa: F401
from argus_dataset import verify


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--compare", metavar="REF.csv", default=None)
    ap.add_argument("--artifact", choices=["lstm_windows", "frame_features"], default=None)
    args = ap.parse_args()

    ok = verify.check_all()
    if args.compare:
        if not args.artifact:
            ap.error("--compare requires --artifact")
        ok &= verify.compare_against(args.compare, args.artifact)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
