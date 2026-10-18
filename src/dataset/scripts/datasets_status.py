#!/usr/bin/env python3
"""Print ``raw/datasets_manifest.json`` as a table and re-check each source against disk.
Also (re)catalogues UTA-RLDD from ``raw/raw_videos/`` so the manifest lists every source.

  python scripts/datasets_status.py            # table + on-disk check
  python scripts/datasets_status.py --classes  # also print each source's class list
"""
import argparse

import argus_dataset.bootstrap  # noqa: F401
from argus_dataset import config, external


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--classes", action="store_true")
    args = ap.parse_args()

    external.catalogue_uta_rldd()
    sources = external.load_manifest()["sources"]
    expected = {"mrl_eye", "uta_rldd"} | {f"roboflow/{s['project']}" for s in config.ROBOFLOW_PROJECTS}

    print(f"{'source':<38} {'status':<14} {'images/clips':>12}  {'licence':<28} on disk")
    for name in sorted(expected | set(sources)):
        e = sources.get(name)
        if e is None:
            print(f"{name:<38} {'NOT PULLED':<14} {'-':>12}  {'-':<28} -")
            continue
        count = e.get("images", e.get("clips", 0))
        print(f"{name:<38} {e.get('status', '?'):<14} {count:>12}  {str(e.get('licence', '?'))[:28]:<28} "
              f"{external.check_source_on_disk(name, e)}")
        if args.classes:
            print(f"    classes: {e.get('class_names') or e.get('classes') or e.get('clips_per_level')}")
            if e.get("boxes_per_class"):
                print(f"    boxes:   {e['boxes_per_class']}")
    missing = sorted(expected - set(sources))
    if missing:
        print(f"\n{len(missing)} source(s) not pulled yet: {', '.join(missing)}")


if __name__ == "__main__":
    main()
