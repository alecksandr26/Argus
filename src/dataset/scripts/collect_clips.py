#!/usr/bin/env python3
"""Collect drowsiness clips into ``raw/raw_videos/subject_NN/level_<1-2>_clip_NN.mp4``.

Record from a webcam, or import existing video files. Clip numbers are allocated as
``max(existing) + 1`` per (subject, label), so collection **never overwrites** a clip. Every
clip is appended to ``raw/raw_videos/collection_log.csv`` the moment it lands.

Examples:
  # record two 20 s "drowsy" clips for a new subject from camera 0
  python scripts/collect_clips.py --subject new --label 2 --count 2 --duration 20

  # import phone recordings as "not drowsy" clips for subject_07
  python scripts/collect_clips.py --subject 7 --not-drowsy --from-file ~/vids/*.mp4

  # what's in the raw tree right now
  python scripts/collect_clips.py --list
"""
import argparse
import sys
from pathlib import Path

import argus_dataset.bootstrap  # noqa: F401
from argus_dataset import assets, collect, paths


def _resolve_level(args) -> int:
    if args.drowsy:
        return 2
    if args.not_drowsy:
        return 1
    if args.label:
        return collect.validate_level(args.label)
    if sys.stdin.isatty():
        ans = input("label — [1] Not Drowsy / [2] Drowsy: ").strip()
        return collect.validate_level(int(ans))
    raise SystemExit("no label: pass --label {1,2} / --drowsy / --not-drowsy")


def _resolve_subject(args) -> str:
    raw = args.subject
    if raw is None:
        raw = input("subject (id, or 'new'): ").strip() if sys.stdin.isatty() else None
    if not raw:
        raise SystemExit("no subject: pass --subject <id|new>")
    return collect.resolve_subject(raw)


def _print_inventory() -> None:
    rows = collect.inventory()
    if not rows:
        print(f"no subjects under {paths.raw_dir()}")
        return
    print(f"  {'subject':<14} {'Not Drowsy':>10} {'Drowsy':>8} {'total':>7}")
    t1 = t2 = 0
    for name, c1, c2 in rows:
        print(f"  {name:<14} {c1:>10} {c2:>8} {c1 + c2:>7}")
        t1 += c1
        t2 += c2
    print(f"  {'-' * 14} {'-' * 10} {'-' * 8} {'-' * 7}")
    print(f"  {f'{len(rows)} subjects':<14} {t1:>10} {t2:>8} {t1 + t2:>7}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subject", help="subject id (7 / 07 / subject_07), or 'new' for the next free one")
    ap.add_argument("--label", type=int, choices=(1, 2), help="1 = Not Drowsy, 2 = Drowsy")
    ap.add_argument("--drowsy", action="store_true", help="shorthand for --label 2")
    ap.add_argument("--not-drowsy", action="store_true", help="shorthand for --label 1")
    ap.add_argument("--count", type=int, default=1, help="clips to record this session (default 1)")
    ap.add_argument("--duration", type=float, default=30.0, help="webcam clip length, seconds (default 30)")
    ap.add_argument("--fps", type=float, default=20.0, help="target webcam capture fps (default 20)")
    ap.add_argument("--camera", type=int, default=0, help="camera index (default 0)")
    ap.add_argument("--resolution", help="capture resolution, e.g. 1280x720")
    ap.add_argument("--no-preview", action="store_true", help="force headless even if a GUI build is present")
    ap.add_argument("--countdown", type=int, default=3, help="pre-roll countdown, seconds (default 3)")
    ap.add_argument("--from-file", nargs="+", metavar="PATH", help="import these video files instead of recording")
    ap.add_argument("--from-dir", metavar="DIR", help="import every video file directly under DIR")
    ap.add_argument("--reencode", action="store_true", help="re-encode imports (default: copy verbatim)")
    ap.add_argument("--import-fps", type=float, help="re-encode imports to this fps")
    ap.add_argument("--no-face-check", action="store_true", help="skip the post-capture face-visibility check")
    ap.add_argument("--list", action="store_true", help="print the raw-tree inventory and exit")
    ap.add_argument("--dry-run", action="store_true", help="show what would be written, do nothing")
    args = ap.parse_args()

    if args.list:
        _print_inventory()
        return

    subject = _resolve_subject(args)
    level = _resolve_level(args)
    subject_dir = paths.raw_dir() / subject

    sources: list[Path | None] = []
    if args.from_dir:
        sources = collect.list_video_files(Path(args.from_dir).expanduser())
        if not sources:
            raise SystemExit(f"no video files directly under {args.from_dir}")
    elif args.from_file:
        sources = [Path(p).expanduser() for p in args.from_file]
    else:
        sources = [None] * max(1, args.count)  # webcam

    resolution = None
    if args.resolution:
        w, h = args.resolution.lower().split("x")
        resolution = (int(w), int(h))

    model_path = None
    if not args.no_face_check and not args.dry_run:
        model_path = assets.ensure_face_detector()

    print(f"subject {subject}  label {level} ({'Drowsy' if level == 2 else 'Not Drowsy'})  "
          f"-> {subject_dir}")

    for src in sources:
        n = collect.next_clip_number(subject_dir, level)
        dest = collect.clip_path(subject, level, n)
        tag = "record from webcam" if src is None else f"import {src}"
        if args.dry_run:
            print(f"  [dry-run] {tag}  ->  {dest.relative_to(paths.raw_dir().parent.parent)}")
            continue

        subject_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n  {tag}  ->  {dest.name}")
        if src is None:
            info = collect.record_webcam(
                dest, camera=args.camera, duration_sec=args.duration, target_fps=args.fps,
                resolution=resolution, preview=not args.no_preview, countdown_sec=args.countdown,
            )
        else:
            info = collect.import_file(
                src, dest, reencode=args.reencode, target_fps=args.import_fps,
            )
        info.subject, info.level, info.clip_number = subject, level, n

        if model_path is not None:
            info.face_coverage = round(collect.quick_face_coverage(dest, model_path), 3)
            if info.face_coverage < 0.6:
                info.notes = (info.notes + "; " if info.notes else "") + "low face coverage"
                print(f"    WARNING: face visible in only {info.face_coverage:.0%} of sampled "
                      "frames — check the camera angle")

        collect.append_collection_log(info)
        print(f"    ok: {info.n_frames} frames @ {info.fps} fps ({info.duration_sec}s), "
              f"{info.width}x{info.height}")

    if not args.dry_run:
        print(f"\nlogged to {paths.collection_log_csv()}")
        print("next: python scripts/update_dataset.py        # see what's new\n"
              "      python scripts/update_dataset.py --run  # process it")


if __name__ == "__main__":
    main()
