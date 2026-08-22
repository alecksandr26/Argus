#!/usr/bin/env python3
"""Extract short, random, non-overlapping sub-clips from UTA-RLDD source videos.

Source dataset: UTA Real-Life Drowsiness Dataset (RLDD)
    https://sites.google.com/view/utarldd/home
    Ghoddoosian, Galib & Athitsos, "A Realistic Dataset and Baseline Temporal Model for
    Early Drowsiness Detection," CVPRW 2019 -- https://arxiv.org/abs/1904.07312
    Downloaded as per-fold zip archives (e.g. "Fold1_part1.zip") from the dataset's Kaggle
    mirror: https://www.kaggle.com/datasets/rishab260/uta-reallife-drowsiness-dataset

Run this locally against a downloaded zip -- it never extracts the whole archive at once
(each ~10-minute source video is pulled to a temp file, processed, then discarded), cuts
CLIPS_PER_SOURCE_VIDEO random non-overlapping sub-clips per source video, and writes them
to --output organized by subject, using Argus's own subject_<NN> folder convention -- numbered
to CONTINUE your existing subject_01..subject_06, not a separate namespace, so the output
mixes into raw_videos/ cleanly (--start-subject controls where the numbering picks up; default
7, i.e. immediately after subject_06). Nothing gets uploaded anywhere automatically -- review
the output, then upload it into Drive's raw_videos/ yourself.

Clip files use "level_<1-3>_clip_<N>.mp4" -- 1=Alert, 2=Low Vigilant, 3=Drowsy, the FINAL class
number, not Argus's own original 1-6 scale. This deliberately reuses the "level_" word but NOT
the 1-6 numbering, which means the metadata-generation loop can't tell these apart from your own
level_<1-6> clips by filename alone -- it now also checks the subject number (see
EXTERNAL_SUBJECT_START in 01_dataset_creation.ipynb): subject_<N> with N >= 7 is treated as
already-final-class (no 1-6->3 mapping applied), subject_01..subject_06 still gets the original
mapping. Keep that in sync if --start-subject is ever set to something other than 7.

Verified against the real archive layout (Fold1_part1.zip): "Fold1_part1/<participant>/
<label>.<ext>", e.g. "Fold1_part1/06/5.mp4" -- participant "06", label "5" (Low Vigilant).
Extensions are mixed-case in practice (.mp4, .mov, .MOV all appear in the same archive).

Usage (run from src/cv-argus/):
    python3 scripts/extract_uta_rldd_clips.py ~/Downloads/Fold1_part1.zip
    python3 scripts/extract_uta_rldd_clips.py ~/Downloads/Fold1_part1.zip ~/Downloads/Fold1_part2.zip --start-subject 7

Requires ffmpeg/ffprobe on PATH.
"""
import argparse
import os
import random
import subprocess
import tempfile
import zipfile
from pathlib import Path

# UTA-RLDD's documented label values: alert=0, low vigilant=5, drowsy=10 -- maps directly to
# Argus's own 3-class scheme (see notebook/01_dataset_creation.ipynb's "Drowsiness levels").
LABEL_TO_CLASS = {"0": 1, "5": 2, "10": 3}
CLASS_NAMES = {1: "alert", 2: "low_vigilant", 3: "drowsy"}
VIDEO_EXTS = {".mp4", ".mov"}  # compared case-insensitively below

# ~3-4 clips per level per subject keeps UTA-RLDD's contribution in the same ballpark as your
# own subjects' clip counts (they mostly have 1-3 raw clips per level) instead of dwarfing them,
# and keeps total runtime down -- more clips = more ffmpeg encodes = more time.
CLIPS_PER_SOURCE_VIDEO = 3
CLIP_DURATION_RANGE_SEC = (60, 180)  # 1-3 minutes
MIN_GAP_SEC = 10  # minimum gap between extracted clips from the same source video
RANDOM_SEED = 42


def guess_class_and_participant(zip_member_name: str):
    """'<.../participant>/<label>.<ext>' -> (class 1-3, participant id), or (None, None)."""
    parts = Path(zip_member_name).parts
    if len(parts) < 2:
        return None, None
    stem = Path(parts[-1]).stem  # "5" from "5.mp4"
    cls = LABEL_TO_CLASS.get(stem)
    if cls is None:
        return None, None
    participant = parts[-2]
    return cls, participant


def assign_subject_numbers(zip_paths, start_subject: int):
    """First pass: collect every (zip_path, participant) pair across all archives, and assign
    each a stable subject_<NN> number starting at start_subject, sorted by (zip, participant) so
    numbering doesn't depend on zip member iteration order."""
    pairs = []
    for zip_path in zip_paths:
        with zipfile.ZipFile(zip_path) as zf:
            participants = set()
            for m in zf.namelist():
                if m.endswith("/") or Path(m).suffix.lower() not in VIDEO_EXTS:
                    continue
                _, participant = guess_class_and_participant(m)
                if participant is not None:
                    participants.add(participant)
        for p in sorted(participants):
            pairs.append((zip_path, p))

    return {pair: start_subject + i for i, pair in enumerate(pairs)}


def ffprobe_duration_sec(video_path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def pick_nonoverlapping_starts(total_duration, n_clips, clip_range, min_gap, rng):
    """Randomly picks up to n_clips non-overlapping (start, duration) pairs within
    [0, total_duration]. Best-effort: returns fewer than n_clips if the video is too short."""
    best = []
    for _attempt in range(200):
        picks = []
        ok = True
        for _ in range(n_clips):
            dur = rng.uniform(*clip_range)
            if total_duration <= dur:
                ok = False
                break
            start = rng.uniform(0, total_duration - dur)
            if any(not (start + dur + min_gap <= s or start >= s + d + min_gap) for s, d in picks):
                ok = False
                break
            picks.append((start, dur))
        if ok:
            return picks
        if len(picks) > len(best):
            best = picks
    return best


def process_zip(zip_path: str, output_dir: Path, rng: random.Random, subject_numbers: dict):
    ingested, skipped = 0, []
    with zipfile.ZipFile(zip_path) as zf:
        members = [
            m for m in zf.namelist()
            if not m.endswith("/") and Path(m).suffix.lower() in VIDEO_EXTS
        ]
        print(f"{zip_path}: {len(members)} source videos")

        for member in members:
            cls, participant = guess_class_and_participant(member)
            if cls is None:
                skipped.append((member, "could not determine class/participant from path"))
                continue

            subject_num = subject_numbers[(zip_path, participant)]
            subject_dir = output_dir / f"subject_{subject_num:02d}"
            subject_dir.mkdir(parents=True, exist_ok=True)

            with tempfile.TemporaryDirectory() as tmp:
                tmp_video = os.path.join(tmp, Path(member).name)
                with zf.open(member) as src, open(tmp_video, "wb") as dst:
                    # Stream in chunks rather than .read() the whole (up to ~1.3GB) file at once
                    for chunk in iter(lambda: src.read(1024 * 1024), b""):
                        dst.write(chunk)

                try:
                    duration = ffprobe_duration_sec(tmp_video)
                except Exception as e:
                    skipped.append((member, f"ffprobe failed: {e}"))
                    continue

                picks = pick_nonoverlapping_starts(
                    duration, CLIPS_PER_SOURCE_VIDEO, CLIP_DURATION_RANGE_SEC, MIN_GAP_SEC, rng
                )
                for clip_n, (start, dur) in enumerate(picks, start=1):
                    out_name = f"level_{cls}_clip_{clip_n:02d}.mp4"
                    out_path = subject_dir / out_name
                    if out_path.exists():
                        continue
                    subprocess.run(
                        ["ffmpeg", "-y", "-ss", str(start), "-i", tmp_video, "-t", str(dur),
                         "-c:v", "libx264", "-c:a", "aac", "-loglevel", "error", str(out_path)],
                        check=True,
                    )
                    ingested += 1
                print(f"  {member} -> subject_{subject_num:02d} "
                      f"({CLASS_NAMES[cls]}, {duration/60:.1f} min) -> {len(picks)} clips")

    return ingested, skipped


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("zips", nargs="+", help="UTA-RLDD zip archive(s), e.g. Fold1_part1.zip")
    parser.add_argument(
        "-o", "--output", default="scripts/output/uta_rldd_clips",
        help="Output directory (default: scripts/output/uta_rldd_clips)",
    )
    parser.add_argument(
        "--start-subject", type=int, default=7,
        help="First subject_<NN> number to assign (default: 7, continuing after your existing "
             "subject_01..subject_06)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(RANDOM_SEED)

    subject_numbers = assign_subject_numbers(args.zips, args.start_subject)
    print("Subject numbering for this run:")
    for (zip_path, participant), num in subject_numbers.items():
        print(f"  {zip_path} participant {participant} -> subject_{num:02d}")
    print()

    total_ingested, all_skipped = 0, []
    for zip_path in args.zips:
        ingested, skipped = process_zip(zip_path, output_dir, rng, subject_numbers)
        total_ingested += ingested
        all_skipped.extend(skipped)

    print(f"\nExtracted {total_ingested} clips total, in {output_dir}/")
    if all_skipped:
        print(f"Skipped {len(all_skipped)} source videos:")
        for member, reason in all_skipped:
            print(f"   - {member}: {reason}")


if __name__ == "__main__":
    main()
