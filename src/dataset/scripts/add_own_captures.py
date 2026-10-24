#!/usr/bin/env python3
"""Turn our own guided captures (scripts/capture_frames.py) into a head-pose source for the
detection dataset, with no Roboflow labelling: head_down comes from the capture tag where the
landmarks agree (see argus_dataset/own_captures.py), everything else from the landmarks.

  python scripts/capture_frames.py --subject new --guided --rounds 2          # record (laptop webcam)
  python scripts/capture_frames.py --subject 56 --tag head_down --from-file head_down.mp4   # or a Pi video
  python scripts/add_own_captures.py                                          # -> raw/roboflow/own-captures/
  python scripts/build_detection_dataset.py --zip                             # -> processed/parts_det.zip

Rebuilds raw/roboflow/own-captures/v1/ from every session under raw/webcam_frames/ on each run.
"""
import argparse
import json

import argus_dataset.bootstrap  # noqa: F401
from tqdm import tqdm

from argus_dataset import detection_dataset as dd
from argus_dataset import config, own_captures, paths


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-sharpness", type=float, default=0.0,
                    help="drop stills whose frames.csv sharpness is below this (0 = keep all)")
    ap.add_argument("--min-pitch", type=float, default=config.OWN_HEAD_DOWN_MIN_PITCH_DEG,
                    help="a head-down still needs at least this pitch (degrees, down positive) to "
                         "be labelled head_down; check pitch_by_tag in the output first")
    args = ap.parse_args()

    dd._mp_init(str(paths.face_detector_path()), str(paths.face_landmarker_path()))
    try:
        summary = own_captures.export(dd.mp_one, args.min_sharpness, args.min_pitch,
                                      progress=lambda it, **k: tqdm(it, **k))
    finally:
        for task in dd._W.values():   # close now: MediaPipe's __del__ at interpreter exit spews errors
            task.close()
    print(json.dumps(summary, indent=2))
    if summary["kept"]:
        print(f"-> {own_captures.project_dir()}\nnext: python scripts/build_detection_dataset.py --zip")
    else:
        print("nothing kept: capture some stills first (scripts/capture_frames.py)")


if __name__ == "__main__":
    main()
