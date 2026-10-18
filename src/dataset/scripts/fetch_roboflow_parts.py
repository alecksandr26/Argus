#!/usr/bin/env python3
"""Download the Roboflow Universe eye/yawn/head projects (``config.ROBOFLOW_PROJECTS``) as YOLO
exports into ``<ARGUS_DATASET_ROOT>/raw/roboflow/<project>/v<version>/`` and record each one
(class list, counts, licence, tree checksum) in ``raw/datasets_manifest.json``. Download-only.

Needs a free Roboflow API key in the environment (never commit it):

  ROBOFLOW_API_KEY=... python scripts/fetch_roboflow_parts.py
  ROBOFLOW_API_KEY=... python scripts/fetch_roboflow_parts.py --projects drowsiness-aunvg
  python scripts/fetch_roboflow_parts.py --list      # just print the configured projects
"""
import argparse

import argus_dataset.bootstrap  # noqa: F401
from argus_dataset import config, external


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--projects", nargs="+", help="subset of project slugs to fetch")
    ap.add_argument("--force", action="store_true", help="re-download even if the tree matches")
    ap.add_argument("--list", action="store_true", help="print configured projects and exit")
    args = ap.parse_args()

    specs = [s for s in config.ROBOFLOW_PROJECTS if not args.projects or s["project"] in args.projects]
    if args.list or not specs:
        for s in config.ROBOFLOW_PROJECTS:
            print(f"{s['workspace']}/{s['project']}  v{s['version'] or 'latest'}  "
                  f"~{s['listed_images']} imgs  — {s['note']}")
        if not specs:
            raise SystemExit(f"no configured project matches {args.projects}")
        return

    rf = external.roboflow_client()
    failed = []
    for spec in specs:
        try:
            e = external.ensure_roboflow_project(spec, rf, force=args.force)
            print(f"  {e['status']:<12} v{e['version']}  {e['images']} imgs  "
                  f"classes={e['class_names']}  licence={e['licence']}")
        except Exception as exc:  # keep going; one broken project shouldn't block the rest
            failed.append(spec["project"])
            print(f"  FAILED {spec['workspace']}/{spec['project']}: {exc}")
    if failed:
        raise SystemExit(f"{len(failed)} project(s) failed: {failed} — re-run to retry just those")


if __name__ == "__main__":
    main()
