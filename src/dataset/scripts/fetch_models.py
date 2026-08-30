#!/usr/bin/env python3
"""Download the MediaPipe model bundles into ``<ARGUS_DATASET_ROOT>/models/``. Idempotent."""
import argus_dataset.bootstrap  # noqa: F401  (must import before native libs)
from argus_dataset import assets, paths


def main() -> None:
    paths.ensure_dirs()
    assets.download_all()


if __name__ == "__main__":
    main()
