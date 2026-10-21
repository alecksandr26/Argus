#!/usr/bin/env python3
"""Capture stills to label for the parts detector (eyes open/closed, yawn, head straight/down).

Saves one full frame every ``--interval`` seconds (default 1) from a webcam, or from existing
videos, into ``raw/webcam_frames/subject_NN/<session>/`` with a ``frames.csv`` per session. The
filename carries a tag (what the person was asked to do) as a hint for whoever labels it.

Examples:
  # guided: straight / eyes closed / head down / head down + eyes closed / yawn, 10 s each, x2
  python scripts/capture_frames.py --subject new --guided --rounds 2

  # free capture for 2 minutes, tagged
  python scripts/capture_frames.py --subject 7 --tag driving --duration 120

  # pull a still per second out of existing recordings
  python scripts/capture_frames.py --subject 7 --tag phone --from-file ~/vids/*.mp4

  # stills per subject per tag so far
  python scripts/capture_frames.py --list
"""
import argparse
import sys
from pathlib import Path

import argus_dataset.bootstrap  # noqa: F401
from argus_dataset import frames, paths


def _print_inventory() -> None:
    inv = frames.inventory()
    if not inv:
        print(f"no stills under {paths.frames_raw_dir()}")
        return
    tags = sorted({t for c in inv.values() for t in c})
    print("  " + f"{'subject':<12}" + "".join(f"{t[:22]:>24}" for t in tags) + f"{'total':>8}")
    for subject, c in sorted(inv.items()):
        print("  " + f"{subject:<12}" + "".join(f"{c[t]:>24}" for t in tags)
              + f"{sum(c.values()):>8}")


def _resolve_subject(raw: str | None) -> str:
    if raw is None and sys.stdin.isatty():
        raw = input("subject (id, or 'new'): ").strip()
    if not raw:
        raise SystemExit("no subject: pass --subject <id|new>")
    return frames.resolve_subject(raw)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subject", help="subject id (7 / 07 / subject_07), or 'new' for the next free one")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between stills (default 1)")
    ap.add_argument("--guided", action="store_true",
                    help="prompt through the poses: " + ", ".join(t for t, _ in frames.DEFAULT_PHASES))
    ap.add_argument("--phases", nargs="+", metavar="TAG",
                    help="guided: only these phase tags, in this order")
    ap.add_argument("--phase-sec", type=float, default=10.0, help="guided: seconds per pose (default 10)")
    ap.add_argument("--transition-sec", type=float, default=3.0,
                    help="guided: un-saved seconds to change pose (default 3)")
    ap.add_argument("--rounds", type=int, default=1, help="guided: times through all poses (default 1)")
    ap.add_argument("--tag", default="free", help="free / --from-file: tag for every still (default free)")
    ap.add_argument("--duration", type=float, default=60.0,
                    help="free: seconds to capture, 0 = until Ctrl-C (default 60)")
    ap.add_argument("--camera", type=int, default=0, help="camera index (default 0)")
    ap.add_argument("--resolution", help="capture resolution, e.g. 1280x720")
    ap.add_argument("--save-video", action="store_true", help="also keep the whole stream as session.mp4")
    ap.add_argument("--no-preview", action="store_true", help="force headless even if a GUI build is present")
    ap.add_argument("--countdown", type=int, default=3, help="pre-roll countdown, seconds (default 3)")
    ap.add_argument("--from-file", nargs="+", metavar="PATH", help="extract stills from these videos instead")
    ap.add_argument("--list", action="store_true", help="print stills per subject per tag and exit")
    args = ap.parse_args()

    if args.list:
        _print_inventory()
        return
    if args.interval <= 0:
        raise SystemExit("--interval must be > 0")

    subject = _resolve_subject(args.subject)
    sessions = []

    if args.from_file:
        for src in (Path(p).expanduser() for p in args.from_file):
            session = frames.new_session(subject)
            print(f"  {src}  ->  {session.dir}")
            res = frames.extract_from_video(src, session, interval_sec=args.interval, tag=args.tag)
            print(f"    {sum(session.saved.values())} stills from {res.duration_sec}s of video")
            sessions.append(session)
    else:
        if args.guided:
            phases = frames.DEFAULT_PHASES
            if args.phases:
                known = dict(frames.DEFAULT_PHASES)
                unknown = [t for t in args.phases if t not in known]
                if unknown:
                    raise SystemExit(f"unknown phase(s) {unknown}; choose from {list(known)}")
                phases = tuple((t, known[t]) for t in args.phases)
            plan = frames.guided_plan(phases, phase_sec=args.phase_sec,
                                      transition_sec=args.transition_sec, rounds=args.rounds)
        else:
            plan = frames.free_plan(args.tag, args.duration)

        resolution = None
        if args.resolution:
            w, h = args.resolution.lower().split("x")
            resolution = (int(w), int(h))

        try:
            cap = frames.open_camera(args.camera, resolution)
        except IOError as e:
            raise SystemExit(f"error: {e}")
        session = frames.new_session(subject)
        total = frames.plan_duration(plan)
        print(f"subject {subject}  ->  {session.dir}\n"
              f"  one still every {args.interval:g}s"
              + ("" if total == float("inf") else f", about {total:.0f}s in total"))
        try:
            res = frames.capture_stream(
                cap, session, plan, interval_sec=args.interval, source=f"webcam:{args.camera}",
                preview=not args.no_preview, countdown_sec=args.countdown,
                save_video=args.save_video,
            )
        finally:
            cap.release()
        if res.notes:
            print(f"  note: {res.notes}")
        if res.video_path:
            print(f"  video: {res.video_path}")
        sessions.append(session)

    for s in sessions:
        counts = ", ".join(f"{t} {n}" for t, n in s.saved.items()) or "nothing"
        print(f"  {s.dir.name}: {counts}")
    print("\nnext: label these images (open_eye / closed_eye / yawn / head_straight / head_down);\n"
          "      see README section 7c for getting them into build_detection_dataset.py")


if __name__ == "__main__":
    main()
