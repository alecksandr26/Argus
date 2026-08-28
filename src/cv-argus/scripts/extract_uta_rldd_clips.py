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
mixes into raw_videos/ cleanly.

Resumable across runs, with no manual bookkeeping required: --output/subject_assignments.json
persists which (zip, participant) pair got which subject_<NN> number, so re-running later --
with the same zips, extra zips, or a mix -- reuses the same numbers for participants already
seen and only assigns fresh numbers (continuing after the highest one used so far, checking
both the assignment file and any subject_<NN> folders already sitting in --output) to newly
seen ones. A video whose subject folder already has a full set of clips for its class is
skipped outright, without re-streaming it out of the zip. --start-subject overrides the
auto-detected next number for any newly-seen participant in this run (e.g. to force a
specific starting point); it never renumbers a participant that's already been assigned.
Nothing gets uploaded anywhere automatically -- review the output, then upload it into
Drive's raw_videos/ yourself.

Clip files use "level_<1-3>_clip_<N>.mp4" -- 1=Alert, 2=Low Vigilant, 3=Drowsy. These are
UTA-RLDD's three native label values (0/5/10) kept as-is: dataset/raw_videos/ is Argus's
3-class source of truth. The model-training pipeline itself is now binary (Not Drowsy vs
Drowsy) -- notebook/relabel_binary_raw_videos.ipynb collapses this 3-class tree into the
derived dataset/raw_videos_binary/ tree (levels 1+2 -> 1, level 3 -> 2) that notebooks
01/02/06/09 actually consume. Producing 3-class clips here keeps that collapse re-derivable
(and a switch to a different binary framing a one-line edit), so don't fold the binary
mapping into this script.

Argus's own raw clips use this same already-final 1-3 convention (they were renamed to it a
while ago), so no per-subject disambiguation is needed -- every clip's filename encodes its
final class directly.

Verified against the real archive layout (Fold1_part1.zip): "Fold1_part1/<participant>/
<label>.<ext>", e.g. "Fold1_part1/06/5.mp4" -- participant "06", label "5" (Low Vigilant).
Extensions are mixed-case in practice (.mp4, .mov, .MOV all appear in the same archive).

Usage (run from src/cv-argus/):
    python3 scripts/extract_uta_rldd_clips.py ~/Downloads/Fold1_part1.zip
    # later, resuming -- already-assigned participants keep their numbers, new ones continue on:
    python3 scripts/extract_uta_rldd_clips.py ~/Downloads/Fold1_part1.zip ~/Downloads/Fold1_part2.zip
    # force a specific starting point for newly-seen participants instead of auto-detecting one:
    python3 scripts/extract_uta_rldd_clips.py ~/Downloads/Fold2_part1.zip --start-subject 20

Requires ffmpeg/ffprobe on PATH.
"""
import argparse
import json
import os
import random
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

# UTA-RLDD's documented label values: alert=0, low vigilant=5, drowsy=10 -- kept as Argus's
# 3-class raw labels (see notebook/01_dataset_creation_lstm.ipynb's "Drowsiness labels", and
# notebook/relabel_binary_raw_videos.ipynb for the downstream binary collapse).
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


DEFAULT_FIRST_SUBJECT = 7  # continuing after Argus's own subject_01..subject_06
SUBJECT_MAP_FILENAME = "subject_assignments.json"


def _subject_map_path(output_dir: Path) -> Path:
    return output_dir / SUBJECT_MAP_FILENAME


def load_subject_map(output_dir: Path) -> dict:
    """Loads the persisted '<zip basename>::<participant>' -> subject number mapping from a
    previous run, if any. Keyed by zip basename (not full path) so resuming works even if the
    zip was re-downloaded to a different directory between runs."""
    path = _subject_map_path(output_dir)
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def save_subject_map(output_dir: Path, mapping: dict) -> None:
    with open(_subject_map_path(output_dir), "w") as f:
        json.dump(mapping, f, indent=2, sort_keys=True)


def find_existing_max_subject(output_dir: Path):
    """Scans --output for subject_<NN> folders already on disk (e.g. from a run whose
    subject_assignments.json got lost/deleted, or Argus's own subject_01..subject_06 living in
    the same tree) and returns the highest NN found, or None if there aren't any."""
    if not output_dir.exists():
        return None
    nums = []
    for p in output_dir.iterdir():
        m = p.is_dir() and re.match(r"subject_(\d+)$", p.name)
        if m:
            nums.append(int(m.group(1)))
    return max(nums) if nums else None


def assign_subject_numbers(zip_paths, output_dir: Path, start_subject):
    """Collects every (zip_path, participant) pair across all archives and assigns each a
    stable subject_<NN> number, reusing whatever subject_assignments.json already has for a
    pair seen in a previous run so re-running with the same/extra zips never renumbers existing
    subjects. Newly-seen pairs get fresh numbers starting at start_subject if given, else
    auto-continuing after the highest number already assigned or already present as a
    subject_<NN> folder in --output (falling back to DEFAULT_FIRST_SUBJECT on a first run)."""
    mapping = load_subject_map(output_dir)

    existing_nums = list(mapping.values())
    existing_dir_max = find_existing_max_subject(output_dir)
    if existing_dir_max is not None:
        existing_nums.append(existing_dir_max)
    auto_next = max(existing_nums) + 1 if existing_nums else DEFAULT_FIRST_SUBJECT
    next_subject = start_subject if start_subject is not None else auto_next
    print(f"Next subject number for newly-seen participants: {next_subject} "
          f"({'explicit --start-subject' if start_subject is not None else 'auto-detected'})")

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

    result = {}
    for zip_path, participant in pairs:
        key = f"{Path(zip_path).name}::{participant}"
        if key in mapping:
            result[(zip_path, participant)] = mapping[key]
        else:
            result[(zip_path, participant)] = next_subject
            mapping[key] = next_subject
            next_subject += 1

    save_subject_map(output_dir, mapping)
    return result


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

            existing_clips = list(subject_dir.glob(f"level_{cls}_clip_*.mp4"))
            if len(existing_clips) >= CLIPS_PER_SOURCE_VIDEO:
                print(f"  {member} -> subject_{subject_num:02d} ({CLASS_NAMES[cls]}) already has "
                      f"{len(existing_clips)} clips, skipping (resume)")
                continue

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
                clips_written = 0
                for clip_n, (start, dur) in enumerate(picks, start=1):
                    out_name = f"level_{cls}_clip_{clip_n:02d}.mp4"
                    out_path = subject_dir / out_name
                    if out_path.exists():
                        clips_written += 1
                        continue
                    try:
                        subprocess.run(
                            # "scale=trunc(iw/2)*2:trunc(ih/2)*2" rounds width/height down to
                            # even -- libx264 requires both dimensions divisible by 2 for 4:2:0
                            # chroma subsampling, and some UTA-RLDD source videos (e.g. a
                            # 480x853 portrait recording) have an odd height and would otherwise
                            # hard-crash the encoder mid-run.
                            ["ffmpeg", "-y", "-ss", str(start), "-i", tmp_video, "-t", str(dur),
                             "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                             "-c:v", "libx264", "-c:a", "aac", "-loglevel", "error", str(out_path)],
                            check=True,
                        )
                    except subprocess.CalledProcessError as e:
                        # ffmpeg can leave a truncated/empty file at out_path before failing
                        # (seen with "-y" on an encoder-open error) -- remove it rather than
                        # leaving a corrupt clip that a later resume run would then treat as
                        # already-done via the out_path.exists() check above.
                        out_path.unlink(missing_ok=True)
                        skipped.append((f"{member} clip {clip_n} ({CLASS_NAMES[cls]})",
                                         f"ffmpeg failed: {e}"))
                        continue
                    ingested += 1
                    clips_written += 1
                print(f"  {member} -> subject_{subject_num:02d} "
                      f"({CLASS_NAMES[cls]}, {duration/60:.1f} min) -> {clips_written}/{len(picks)} clips")

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
        "--start-subject", type=int, default=None,
        help="First subject_<NN> number to assign to any newly-seen participant in this run. "
             "Default: auto -- continue after the highest subject_<NN> already recorded in "
             "--output's subject_assignments.json or already present as a folder there, "
             "falling back to 7 (after your existing subject_01..subject_06) on a first run. "
             "A participant already assigned a number in a previous run keeps it regardless of "
             "this flag.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(RANDOM_SEED)

    subject_numbers = assign_subject_numbers(args.zips, output_dir, args.start_subject)
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
