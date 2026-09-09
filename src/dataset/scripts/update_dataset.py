#!/usr/bin/env python3
"""Incremental dataset update: what's new, what's stale, and run the builds for it.

The four builds are already incremental — re-running any ``build_*.py`` processes only clips
it hasn't seen and appends to the CSV. This script is the cross-artifact view around that:

  update_dataset.py               # status table: per artifact -> done / new / orphan
  update_dataset.py --run         # run every build that has new work (same as run_all.sh)
  update_dataset.py --run --enrich # ... and rewrite frame_features_enriched.csv
  update_dataset.py --prune       # drop rows whose raw .mp4 was deleted/renamed (asks first)

Default (no flags) is read-only.
"""
import argparse
import sys

import argus_dataset.bootstrap  # noqa: F401
from argus_dataset import cli, paths, update


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="store_true", help="run the incremental build chain")
    ap.add_argument("--prune", action="store_true", help="remove orphan rows + orphaned crop JPEGs")
    ap.add_argument("--enrich", action="store_true", help="with --run, also rewrite frame_features_enriched.csv")
    ap.add_argument("--yes", action="store_true", help="skip the --prune confirmation prompt")
    ap.add_argument("--workers", type=int, default=None, help="worker processes (default: auto)")
    ap.add_argument("--subjects", default=None,
                    help="comma-separated subject folders to limit --run to, e.g. subject_55,subject_56")
    args = ap.parse_args()

    raw = update.raw_keys()
    if not raw:
        print(f"no .mp4 clips under {paths.raw_dir()} — collect some with scripts/collect_clips.py")
        raise SystemExit(1)

    rows = update.status(raw)
    print(update.format_status(rows))

    if args.prune:
        orphans = {k for r in rows for k in r.orphan}
        if not orphans:
            print("\nnothing to prune.")
        else:
            if not args.yes and sys.stdin.isatty():
                if input(f"\nprune {len(orphans)} orphaned clip(s) from all artifacts? [y/N] ").strip().lower() != "y":
                    raise SystemExit("aborted.")
            res = update.prune(raw)
            print()
            for artifact, (logs, csv_rows) in res.items():
                print(f"  {artifact:<20} dropped {logs} log entr(ies), {csv_rows} CSV row(s)")
            print("  -> re-run with --run (or build_cnn_lstm_windows.py) to rebuild the window index\n")
            rows = update.status(raw)
            print(update.format_status(rows))

    if args.run:
        print("\nrunning incremental build chain...\n")
        raise SystemExit(update.run(
            workers_n=args.workers,
            subjects=cli.subjects_list(args),
            enrich=args.enrich,
        ))


if __name__ == "__main__":
    main()
