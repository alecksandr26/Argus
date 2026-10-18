#!/usr/bin/env python3
"""Download + unpack the MRL Eye dataset into ``<ARGUS_DATASET_ROOT>/raw/mrl_eye/`` and record it
in ``raw/datasets_manifest.json``. Download-only (no cropping / resizing). Idempotent: skips when
the archive checksum and extracted image count already match the manifest.

  python scripts/fetch_mrl_eye.py            # download (~342 MB) + unpack + catalogue
  python scripts/fetch_mrl_eye.py --force    # re-download and re-extract
"""
import argparse
import json

import argus_dataset.bootstrap  # noqa: F401
from argus_dataset import external


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="re-download and re-extract")
    entry = external.ensure_mrl_eye(force=ap.parse_args().force)
    print(json.dumps({k: entry[k] for k in ("status", "images", "subjects", "classes", "sensors")}, indent=2))


if __name__ == "__main__":
    main()
