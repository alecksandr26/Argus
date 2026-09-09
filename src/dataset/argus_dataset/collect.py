"""Clip collection — record from a webcam or import existing video files into the raw tree.

The dataset builds (``build_lstm_windows`` / ``build_frame_features`` / ``build_face_crops`` /
``build_cnn_lstm_windows``) read ``raw/raw_videos/subject_NN/level_<1-2>_clip_NN.mp4``, already
binary-labelled (``level_1`` = Not Drowsy, ``level_2`` = Drowsy). Getting a clip into that tree
by hand means naming it exactly right and picking a clip number that doesn't collide with an
existing one. This module does that:

  * :func:`normalize_subject` / :func:`next_subject_id` — subject-folder naming.
  * :func:`next_clip_number` / :func:`clip_path` — the **no-overwrite** clip-number allocation.
  * :func:`record_webcam` — capture ``duration_sec`` of a camera to an mp4.
  * :func:`import_file` — file an existing video (copy, or re-encode) into the tree.
  * :func:`append_collection_log` — append one provenance row to
    ``raw/raw_videos/collection_log.csv`` the moment a clip is finalised (write-as-you-go;
    never batched).
  * :func:`quick_face_coverage` — optional sanity check: is a face actually visible?

``cv2`` is imported lazily inside the functions so importing this module stays cheap and
doesn't pull native libraries before they're needed. The current dependency is
``opencv-python-headless`` (no GUI); :func:`record_webcam` falls back to a headless progress
line if ``cv2.imshow`` isn't available. ``pip install 'argus-dataset[collect]'`` swaps in the
full ``opencv-python`` build for a live preview window (don't install both in one env).
"""

from __future__ import annotations

import contextlib
import csv
import os
import re
import shutil
import signal
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config, paths

_SUBJECT_RE = re.compile(r"^subject_(\d+)$")
_CLIP_RE = re.compile(r"^level_(\d+)_clip_(\d+)\.mp4$", re.IGNORECASE)
_VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm"}

_COLLECTION_LOG_COLS = [
    "timestamp", "subject", "level", "clip_number", "filename", "source",
    "duration_sec", "fps", "n_frames", "width", "height", "face_coverage", "notes",
]


@dataclass
class ClipInfo:
    """What actually landed on disk for one collected clip."""

    path: str
    subject: str
    level: int
    clip_number: int
    source: str            # "webcam:0" or the absolute source path
    n_frames: int
    fps: float
    duration_sec: float
    width: int
    height: int
    face_coverage: float | None = None
    notes: str = ""


# --------------------------------------------------------------------------------------------
# Naming — subject folders and the no-overwrite clip-number allocation
# --------------------------------------------------------------------------------------------

def normalize_subject(raw: str) -> str:
    """``"7"`` / ``"07"`` / ``"subject_7"`` / ``"subject_07"`` -> ``"subject_07"``.

    Zero-pads to at least two digits to match the existing ``subject_01``..``subject_54`` pool.
    """
    s = raw.strip().lower()
    m = _SUBJECT_RE.match(s)
    digits = m.group(1) if m else s
    if not digits.isdigit():
        raise ValueError(
            f"can't read a subject number from {raw!r} — use e.g. 7, 07, or subject_07"
        )
    return f"subject_{int(digits):02d}"


def _subject_numbers() -> list[int]:
    root = paths.raw_dir()
    if not root.exists():
        return []
    return sorted(
        int(m.group(1))
        for p in root.iterdir()
        if p.is_dir() and (m := _SUBJECT_RE.match(p.name))
    )


def next_subject_id() -> str:
    """The next free ``subject_NN`` after the highest one already in the raw tree."""
    nums = _subject_numbers()
    return f"subject_{(nums[-1] + 1) if nums else 1:02d}"


def resolve_subject(raw: str) -> str:
    """CLI helper: ``"new"`` -> :func:`next_subject_id`, anything else -> :func:`normalize_subject`."""
    return next_subject_id() if raw.strip().lower() in {"new", "next"} else normalize_subject(raw)


def next_clip_number(subject_dir: Path, level: int) -> int:
    """``max(existing level_<level>_clip_NN) + 1``, or 1 if there are none. This is what keeps
    collection from ever overwriting a clip."""
    if not subject_dir.exists():
        return 1
    nums = [
        int(m.group(2))
        for p in subject_dir.iterdir()
        if (m := _CLIP_RE.match(p.name)) and int(m.group(1)) == level
    ]
    return (max(nums) + 1) if nums else 1


def clip_path(subject: str, level: int, clip_number: int) -> Path:
    """Canonical path for one clip. Raises if it already exists (collection never overwrites)."""
    p = paths.raw_dir() / subject / f"level_{level}_clip_{clip_number:02d}.mp4"
    if p.exists():
        raise FileExistsError(f"{p} already exists — refusing to overwrite")
    return p


def validate_level(level: int) -> int:
    """Accept only the binary scheme. Reuses :func:`config.map_level`'s validation/messaging."""
    return config.map_level(int(level))


# --------------------------------------------------------------------------------------------
# Capture — webcam
# --------------------------------------------------------------------------------------------

def _preview_available(cv2) -> bool:
    if not hasattr(cv2, "imshow"):
        return False
    try:
        cv2.namedWindow("__argus_probe__", cv2.WINDOW_AUTOSIZE)
        cv2.destroyWindow("__argus_probe__")
        return True
    except cv2.error:
        return False


def record_webcam(
    out_path: Path,
    *,
    camera: int = 0,
    duration_sec: float = 30.0,
    target_fps: float = 20.0,
    resolution: tuple[int, int] | None = None,
    preview: bool = True,
    countdown_sec: int = 3,
) -> ClipInfo:
    """Record ``duration_sec`` of ``camera`` to ``out_path`` (mp4v).

    Stops early on SIGINT (keeps what's recorded) or, when a preview window is up, on ``q``.
    The file is written at the *measured* effective fps (frames / wall-clock), so playback
    duration is honest and the builds' fixed 5-fps downsample stays correct.
    """
    import cv2  # lazy

    cap = cv2.VideoCapture(camera)
    if not cap.isOpened():
        cap.release()
        raise IOError(f"cannot open camera index {camera}")
    if resolution:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, resolution[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, resolution[1])

    show = preview and _preview_available(cv2)
    if preview and not show:
        print("  (preview unavailable — opencv-python-headless has no GUI; recording headless. "
              "Ctrl-C stops early.)")

    # warm up + countdown
    for _ in range(5):
        cap.read()
    for n in range(countdown_sec, 0, -1):
        print(f"  recording in {n}...", flush=True)
        t0 = time.time()
        while time.time() - t0 < 1.0:
            ok, frame = cap.read()
            if show and ok:
                cv2.putText(frame, str(n), (40, 90), cv2.FONT_HERSHEY_SIMPLEX,
                            3.0, (0, 0, 255), 4)
                cv2.imshow("argus collect", frame)
                cv2.waitKey(1)

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or (resolution[0] if resolution else 640)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or (resolution[1] if resolution else 480)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    tmp_path = out_path.with_suffix(".mp4.partial")
    writer = cv2.VideoWriter(str(tmp_path), fourcc, float(target_fps), (w, h))

    stop = {"flag": False}

    def _on_sigint(_sig, _frame):
        stop["flag"] = True

    old = signal.signal(signal.SIGINT, _on_sigint)
    n_frames = 0
    start = time.time()
    try:
        while not stop["flag"]:
            elapsed = time.time() - start
            if elapsed >= duration_sec:
                break
            ok, frame = cap.read()
            if not ok:
                break
            writer.write(frame)
            n_frames += 1
            if show:
                cv2.putText(frame, f"REC {elapsed:4.1f}/{duration_sec:.0f}s  {out_path.name}",
                            (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                cv2.imshow("argus collect", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            elif n_frames % max(1, int(target_fps)) == 0:
                print(f"\r  recording {elapsed:4.1f}/{duration_sec:.0f}s ({n_frames} frames)",
                      end="", flush=True)
    finally:
        signal.signal(signal.SIGINT, old)
        cap.release()
        writer.release()
        if show:
            with contextlib.suppress(Exception):
                cv2.destroyAllWindows()
        print()

    wall = max(time.time() - start, 1e-6)
    effective_fps = n_frames / wall
    if n_frames == 0:
        tmp_path.unlink(missing_ok=True)
        raise IOError("captured 0 frames — camera returned nothing")

    # rewrite the container header at the measured fps
    _rewrite_fps(tmp_path, out_path, effective_fps)
    tmp_path.unlink(missing_ok=True)

    return ClipInfo(
        path=str(out_path), subject=out_path.parent.name, level=0, clip_number=0,
        source=f"webcam:{camera}", n_frames=n_frames, fps=round(effective_fps, 3),
        duration_sec=round(wall, 3), width=w, height=h,
        notes="stopped early" if stop["flag"] else "",
    )


def _rewrite_fps(src: Path, dst: Path, fps: float) -> None:
    """Re-mux ``src`` -> ``dst`` at ``fps`` (OpenCV decode/encode; no ffmpeg dependency)."""
    import cv2

    cap = cv2.VideoCapture(str(src))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    writer = cv2.VideoWriter(str(dst), cv2.VideoWriter_fourcc(*"mp4v"), float(fps), (w, h))
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            writer.write(frame)
    finally:
        cap.release()
        writer.release()


# --------------------------------------------------------------------------------------------
# Capture — import an existing file
# --------------------------------------------------------------------------------------------

def probe_video(path: Path) -> tuple[int, float, int, int]:
    """``(n_frames, fps, width, height)`` for ``path``. Raises if it won't open / has no frames."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        cap.release()
        raise IOError(f"cannot open video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if n <= 0:  # some containers don't report a count — walk it
        n = 0
        while cap.read()[0]:
            n += 1
    cap.release()
    if n <= 0:
        raise IOError(f"video has no readable frames: {path}")
    return n, float(fps), w, h


def import_file(
    src: Path,
    out_path: Path,
    *,
    reencode: bool = False,
    target_fps: float | None = None,
) -> ClipInfo:
    """File ``src`` into the raw tree at ``out_path``. Copies verbatim unless ``reencode`` or
    ``target_fps`` is set, in which case it's decoded/re-encoded (mp4v)."""
    n, fps, w, h = probe_video(src)

    if reencode or target_fps:
        out_fps = float(target_fps) if target_fps else (fps or 20.0)
        _rewrite_fps(src, out_path, out_fps)
        n, fps, w, h = probe_video(out_path)
    else:
        shutil.copy2(src, out_path)

    dur = (n / fps) if fps else 0.0
    return ClipInfo(
        path=str(out_path), subject=out_path.parent.name, level=0, clip_number=0,
        source=str(src.resolve()), n_frames=n, fps=round(fps, 3),
        duration_sec=round(dur, 3), width=w, height=h,
    )


def list_video_files(directory: Path) -> list[Path]:
    """Video files directly under ``directory``, filename-sorted (import order)."""
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in _VIDEO_SUFFIXES
    )


# --------------------------------------------------------------------------------------------
# Provenance log — one row per clip, written the moment it lands
# --------------------------------------------------------------------------------------------

def append_collection_log(info: ClipInfo) -> None:
    """Append ``info`` as one row to ``raw/raw_videos/collection_log.csv`` (header written if the
    file is new), ``fsync``'d. Called immediately after each clip is finalised — never batched."""
    log = paths.collection_log_csv()
    log.parent.mkdir(parents=True, exist_ok=True)
    d = asdict(info)
    row = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "subject": d["subject"], "level": d["level"], "clip_number": d["clip_number"],
        "filename": Path(d["path"]).name, "source": d["source"],
        "duration_sec": d["duration_sec"], "fps": d["fps"], "n_frames": d["n_frames"],
        "width": d["width"], "height": d["height"],
        "face_coverage": "" if d["face_coverage"] is None else d["face_coverage"],
        "notes": d["notes"],
    }
    new = not log.exists() or log.stat().st_size == 0
    with open(log, "a", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=_COLLECTION_LOG_COLS)
        if new:
            wr.writeheader()
        wr.writerow(row)
        fh.flush()
        os.fsync(fh.fileno())


# --------------------------------------------------------------------------------------------
# Optional sanity check — is a face visible?
# --------------------------------------------------------------------------------------------

def quick_face_coverage(video_path: Path, model_path: Path, n_samples: int = 12) -> float:
    """Fraction of ~``n_samples`` evenly-spaced frames in which the BlazeFace detector finds a
    face. A low value at collection time usually means a bad camera angle. Lazy MediaPipe."""
    import cv2
    import mediapipe as mp

    from .pipelines import _make_detector

    cap = cv2.VideoCapture(str(video_path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    if total <= 0:
        cap.release()
        return 0.0
    idxs = [int(i * total / n_samples) for i in range(n_samples)]
    detector = _make_detector(str(model_path), "IMAGE", config.CROP_MIN_DETECTION_CONFIDENCE)
    hits = 0
    try:
        for i in idxs:
            cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ok, frame = cap.read()
            if not ok:
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
            if res.detections:
                hits += 1
    finally:
        cap.release()
        detector.close()
    return hits / len(idxs)


# --------------------------------------------------------------------------------------------
# Inventory — for `collect_clips.py --list`
# --------------------------------------------------------------------------------------------

def inventory() -> list[tuple[str, int, int]]:
    """``[(subject, n_level_1, n_level_2), ...]`` across the raw tree, subject-sorted."""
    root = paths.raw_dir()
    out: list[tuple[str, int, int]] = []
    if not root.exists():
        return out
    for sub in sorted(p for p in root.iterdir() if p.is_dir() and _SUBJECT_RE.match(p.name)):
        c1 = c2 = 0
        for p in sub.iterdir():
            m = _CLIP_RE.match(p.name)
            if not m:
                continue
            if int(m.group(1)) == 1:
                c1 += 1
            elif int(m.group(1)) == 2:
                c2 += 1
        out.append((sub.name, c1, c2))
    return out
