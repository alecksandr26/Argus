#!/usr/bin/env python3
"""Package the built artifacts for the Colab training notebooks and upload them to Drive.

Steps:
  1. verify (skip with --skip-verify)
  2. write ``*_forcolab.csv`` copies of the two index CSVs with the local ``image_path`` prefix
     rewritten to the Colab Drive path — notebooks 07/10 read those paths literally.
  3. ``tar`` ``processed/face_crops/`` into one uncompressed archive (thousands of small JPEGs
     upload painfully one-by-one).
  4. ``rclone copy`` the CSVs + tar to ``$ARGUS_RCLONE_REMOTE`` (or print manual instructions).

Set ``ARGUS_RCLONE_REMOTE`` to e.g. ``gdrive:Argus/dataset/dataset_processed`` after
``rclone config``.
"""
import argparse
import os
import shutil
import subprocess
import sys

import argus_dataset.bootstrap  # noqa: F401
from argus_dataset import paths, verify

DEFAULT_COLAB_PROCESSED = "/content/drive/MyDrive/Argus/dataset/dataset_processed"
_PATH_CSVS = ("face_crops_index.csv", "cnn_lstm_windows_index.csv")
_PLAIN_CSVS = ("lstm_windows.csv", "frame_features.csv", "frame_features_enriched.csv")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-verify", action="store_true")
    ap.add_argument("--colab-processed-dir", default=DEFAULT_COLAB_PROCESSED)
    ap.add_argument("--no-tar", action="store_true", help="skip the face_crops/ tar")
    ap.add_argument("--no-upload", action="store_true", help="prep files but don't rclone")
    args = ap.parse_args()

    processed = paths.processed_dir()
    if not args.skip_verify and not verify.check_all():
        print("\nverify failed — fix the artifacts or pass --skip-verify.", file=sys.stderr)
        raise SystemExit(1)

    staged: list[str] = []

    import pandas as pd
    local_prefix = str(processed)
    for name in _PATH_CSVS:
        src = processed / name
        if not src.exists():
            continue
        df = pd.read_csv(src)
        for col in ("image_path", "image_paths"):
            if col in df.columns:
                df[col] = df[col].str.replace(local_prefix, args.colab_processed_dir, regex=False)
        out = processed / name.replace(".csv", "_forcolab.csv")
        df.to_csv(out, index=False)
        staged.append(str(out))
        print(f"rewrote paths -> {out.name}")

    for name in _PLAIN_CSVS:
        p = processed / name
        if p.exists():
            staged.append(str(p))

    if not args.no_tar and paths.face_crops_dir().exists():
        tar_path = processed / "face_crops.tar"
        print(f"tarring {paths.face_crops_dir()} -> {tar_path.name} ...")
        subprocess.run(["tar", "-cf", str(tar_path), "-C", str(processed), "face_crops"], check=True)
        staged.append(str(tar_path))

    print("\nStaged for upload:")
    for s in staged:
        print(f"  {s}  ({os.path.getsize(s) / 1e6:.1f} MB)")

    remote = os.environ.get("ARGUS_RCLONE_REMOTE")
    if args.no_upload or not remote or not shutil.which("rclone"):
        print("\nNo upload performed. To upload:")
        if not shutil.which("rclone"):
            print("  install rclone + `rclone config` a Google Drive remote")
        print("  export ARGUS_RCLONE_REMOTE=gdrive:Argus/dataset/dataset_processed")
        for s in staged:
            print(f"  rclone copy {s} $ARGUS_RCLONE_REMOTE")
    else:
        for s in staged:
            print(f"rclone copy {s} -> {remote}")
            subprocess.run(["rclone", "copy", "--progress", s, remote], check=True)

    print(
        "\nOn Colab, after the files are in Drive:\n"
        "  import tarfile, os\n"
        f"  os.chdir('{args.colab_processed_dir}')\n"
        "  tarfile.open('face_crops.tar').extractall()\n"
        "  # then point the training notebooks at *_forcolab.csv for the crop-path CSVs."
    )


if __name__ == "__main__":
    main()
