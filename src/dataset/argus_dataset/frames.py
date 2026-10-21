"""Frame capture for hand labelling — one still every ``interval_sec`` from a webcam or a video.

The detector notebooks (12/13/14) train on ``processed/parts_det/``, which
``build_detection_dataset.py`` builds from **full labelled images** (BlazeFace finds the face and
makes the square crop itself). The external Roboflow sets are thin on our own conditions, so this
module produces stills of real people in front of our camera, to label (eyes open/closed, yawn,
head straight/down) and feed back in as one more source.

Layout, under ``raw/webcam_frames/`` (gitignored with the rest of ``raw/``)::

    subject_NN/<YYYYmmdd-HHMMSS>/
        subject_NN_<session>_0001_<tag>.jpg   full frame, never drawn on
        frames.csv                            one row per still, written as it lands
        session.mp4                           optional (--save-video), the whole stream

``tag`` is a **hint** for the labeller (what the person was asked to do when the still was
taken, e.g. ``eyes_closed``), not a label: people blink, look away and start a pose late.

Guided mode walks the person through :data:`DEFAULT_PHASES` with a short un-saved transition
between phases, so each pose gets a balanced number of stills and none are taken mid-change.

``cv2`` is imported lazily, as in :mod:`collect`.
"""

from __future__ import annotations

import contextlib
import csv
import math
import os
import signal
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import paths
from .collect import _SUBJECT_RE, normalize_subject

# (tag, prompt shown to the person). Covers the detector's classes: open/closed eyes, yawn,
# head straight/down — and the hard case of both at once.
DEFAULT_PHASES: tuple[tuple[str, str], ...] = (
    ("straight", "Look straight ahead, eyes open"),
    ("eyes_closed", "Close your eyes, head straight"),
    ("head_down", "Drop your head forward, eyes open"),
    ("head_down_eyes_closed", "Head down AND eyes closed (nodding off)"),
    ("yawn", "Yawn - open your mouth wide"),
)

FRAMES_CSV = "frames.csv"
_FRAMES_COLS = [
    "filename", "subject", "session", "index", "tag", "t_sec", "timestamp", "source",
    "width", "height", "sharpness",
]


# --------------------------------------------------------------------------------------------
# Capture plan — what the person should be doing at each moment, and whether to save
# --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Segment:
    start: float
    end: float          # math.inf = until stopped
    tag: str
    prompt: str
    save: bool


def guided_plan(
    phases=DEFAULT_PHASES, *, phase_sec: float = 10.0, transition_sec: float = 3.0,
    rounds: int = 1,
) -> list[Segment]:
    """``rounds`` × (transition, phase) for each phase. Transitions are not saved."""
    plan: list[Segment] = []
    t = 0.0
    for _ in range(max(1, rounds)):
        for tag, prompt in phases:
            if transition_sec > 0:
                plan.append(Segment(t, t + transition_sec, tag, f"Get ready: {prompt}", False))
                t += transition_sec
            plan.append(Segment(t, t + phase_sec, tag, prompt, True))
            t += phase_sec
    return plan


def free_plan(tag: str = "free", duration_sec: float = 60.0) -> list[Segment]:
    """One saved segment; ``duration_sec <= 0`` runs until Ctrl-C / ``q``."""
    end = duration_sec if duration_sec > 0 else math.inf
    return [Segment(0.0, end, tag, f"Recording ({tag})", True)]


def segment_at(plan: list[Segment], t: float) -> Segment | None:
    """The segment covering ``t`` seconds, or ``None`` once the plan is over."""
    for seg in plan:
        if seg.start <= t < seg.end:
            return seg
    return None


def plan_duration(plan: list[Segment]) -> float:
    return plan[-1].end if plan else 0.0


# --------------------------------------------------------------------------------------------
# Session — one capture run's folder + its write-as-you-go manifest
# --------------------------------------------------------------------------------------------

def next_subject_id() -> str:
    """Highest ``subject_NN`` across ``raw_videos/`` **and** ``webcam_frames/``, plus one: one
    namespace, so a person recorded both ways keeps one id (the split keys on it)."""
    nums = []
    for root in (paths.raw_dir(), paths.frames_raw_dir()):
        if root.exists():
            nums += [int(m.group(1)) for p in root.iterdir()
                     if p.is_dir() and (m := _SUBJECT_RE.match(p.name))]
    return f"subject_{max(nums, default=0) + 1:02d}"


def resolve_subject(raw: str) -> str:
    """``"new"`` -> :func:`next_subject_id`, anything else -> :func:`collect.normalize_subject`."""
    return next_subject_id() if raw.strip().lower() in {"new", "next"} else normalize_subject(raw)


@dataclass
class Session:
    subject: str
    name: str
    dir: Path
    saved: Counter = field(default_factory=Counter)

    def frame_path(self, index: int, tag: str) -> Path:
        return self.dir / f"{self.subject}_{self.name}_{index:04d}_{tag}.jpg"

    @property
    def csv_path(self) -> Path:
        return self.dir / FRAMES_CSV


def new_session(subject: str, now: float | None = None) -> Session:
    """Create ``raw/webcam_frames/<subject>/<YYYYmmdd-HHMMSS>[_N]/``. Never reuses a folder."""
    base = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    parent = paths.frames_raw_dir() / subject
    parent.mkdir(parents=True, exist_ok=True)
    name, n = base, 1
    while True:
        try:
            (parent / name).mkdir()
            return Session(subject, name, parent / name)
        except FileExistsError:
            n += 1
            name = f"{base}_{n}"


def sharpness(frame) -> float:
    """Variance of the Laplacian on the gray frame — low values are motion-blurred stills."""
    import cv2
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def save_still(session: Session, frame, *, tag: str, t_sec: float, source: str,
               jpeg_quality: int = 95) -> Path:
    """Write one still + its ``frames.csv`` row (``fsync``'d), and return the image path."""
    import cv2

    index = sum(session.saved.values()) + 1
    out = session.frame_path(index, tag)
    if not cv2.imwrite(str(out), frame, [cv2.IMWRITE_JPEG_QUALITY, int(jpeg_quality)]):
        raise IOError(f"could not write {out}")
    h, w = frame.shape[:2]
    row = {
        "filename": out.name, "subject": session.subject, "session": session.name,
        "index": index, "tag": tag, "t_sec": round(t_sec, 3),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": source,
        "width": w, "height": h, "sharpness": round(sharpness(frame), 1),
    }
    new = not session.csv_path.exists()
    with open(session.csv_path, "a", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=_FRAMES_COLS)
        if new:
            wr.writeheader()
        wr.writerow(row)
        fh.flush()
        os.fsync(fh.fileno())
    session.saved[tag] += 1
    return out


# --------------------------------------------------------------------------------------------
# Capture — live camera
# --------------------------------------------------------------------------------------------

@dataclass
class CaptureResult:
    session: Session
    n_read: int
    duration_sec: float
    stopped_early: bool
    video_path: Path | None = None
    notes: str = ""


def open_camera(camera: int, resolution: tuple[int, int] | None = None):
    import cv2

    cap = cv2.VideoCapture(camera)
    if not cap.isOpened():
        cap.release()
        raise IOError(f"cannot open camera index {camera} (is a /dev/video* device attached?)")
    if resolution:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, resolution[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, resolution[1])
    return cap


def capture_stream(
    cap,
    session: Session,
    plan: list[Segment],
    *,
    interval_sec: float = 1.0,
    source: str = "webcam:0",
    preview: bool = True,
    countdown_sec: int = 3,
    save_video: bool = False,
    video_fps: float = 20.0,
    jpeg_quality: int = 95,
    clock=time.monotonic,
) -> CaptureResult:
    """Read ``cap`` continuously (so the camera buffer never goes stale) and save a still every
    ``interval_sec`` while the current plan segment says to. Stops at the end of the plan, on
    SIGINT (keeps what's saved), on ``q`` in the preview, or when the camera stops returning
    frames. Does not release ``cap``."""
    import cv2

    from .collect import _preview_available, _rewrite_fps

    show = preview and _preview_available(cv2)
    if preview and not show:
        print("  (no preview window: opencv-python-headless has no GUI. Prompts print here; "
              "Ctrl-C stops early.)")

    stop = {"flag": False}

    def _on_sigint(_sig, _frame):
        stop["flag"] = True

    old = signal.signal(signal.SIGINT, _on_sigint)
    writer = None
    tmp_video = session.dir / ".session.partial.mp4"   # must end in .mp4, see collect.py
    n_read = 0
    notes = ""
    start = clock()
    try:
        # warm-up + countdown (frames read, never saved)
        for _ in range(5):
            cap.read()
        for n in range(countdown_sec, 0, -1):
            print(f"  starting in {n}...", flush=True)
            t0 = clock()
            while clock() - t0 < 1.0 and not stop["flag"]:
                ok, frame = cap.read()
                if show and ok:
                    _overlay(cv2, frame, str(n), big=True)
                    cv2.imshow("argus capture", frame)
                    cv2.waitKey(1)

        start = clock()
        next_due = 0.0
        current: Segment | None = None
        while not stop["flag"]:
            t = clock() - start
            seg = segment_at(plan, t)
            if seg is None:
                break
            ok, frame = cap.read()
            if not ok:
                notes = "camera stopped returning frames"
                break
            n_read += 1

            if save_video:
                if writer is None:
                    h, w = frame.shape[:2]
                    writer = cv2.VideoWriter(str(tmp_video), cv2.VideoWriter_fourcc(*"mp4v"),
                                             float(video_fps), (w, h))
                    if not writer.isOpened():
                        raise IOError(f"cannot open a video writer for {tmp_video}")
                writer.write(frame)

            if seg is not current:
                current = seg
                print(f"\a\n  >>> {seg.prompt}" + ("" if seg.save else "  (not saving)"),
                      flush=True)

            if t >= next_due:
                while next_due <= t:
                    next_due += interval_sec
                if seg.save:
                    save_still(session, frame, tag=seg.tag, t_sec=t, source=source,
                               jpeg_quality=jpeg_quality)
                    if not show:
                        print(f"\r  {seg.tag:<24} saved {session.saved[seg.tag]:>3}  "
                              f"(total {sum(session.saved.values())})", end="", flush=True)

            if show:
                left = "" if math.isinf(seg.end) else f"  {seg.end - t:4.1f}s"
                _overlay(cv2, frame, seg.prompt + left, big=False,
                         color=(0, 0, 255) if seg.save else (0, 200, 255))
                _overlay(cv2, frame, f"saved {sum(session.saved.values())}", big=False,
                         y=frame.shape[0] - 16)
                cv2.imshow("argus capture", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    stop["flag"] = True
    finally:
        signal.signal(signal.SIGINT, old)
        if writer is not None:
            writer.release()
        if show:
            with contextlib.suppress(Exception):
                cv2.destroyAllWindows()
        print()

    wall = max(clock() - start, 1e-6)
    video_path = None
    if writer is not None and n_read:
        # re-mux at the measured fps so playback duration is honest (same as collect.py)
        video_path = session.dir / "session.mp4"
        _rewrite_fps(tmp_video, video_path, n_read / wall)
    tmp_video.unlink(missing_ok=True)

    return CaptureResult(session, n_read, round(wall, 3), stop["flag"], video_path, notes)


def _overlay(cv2, frame, text, *, big, color=(0, 0, 255), y=None):
    scale, thick = (3.0, 4) if big else (0.7, 2)
    y = y if y is not None else (90 if big else 30)
    cv2.putText(frame, text, (12, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 3)
    cv2.putText(frame, text, (12, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick)


# --------------------------------------------------------------------------------------------
# Capture — an existing video file (phone recordings, collect_clips.py clips, ...)
# --------------------------------------------------------------------------------------------

def extract_from_video(
    video: Path, session: Session, *, interval_sec: float = 1.0, tag: str = "free",
    jpeg_quality: int = 95,
) -> CaptureResult:
    """One still every ``interval_sec`` of **video** time (not wall time)."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        cap.release()
        raise IOError(f"cannot open video: {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    if fps <= 0:
        cap.release()
        raise IOError(f"{video} reports no fps; re-encode it first")
    n_read, next_due = 0, 0.0
    try:
        while cap.grab():
            t = n_read / fps
            n_read += 1
            if t < next_due:
                continue
            while next_due <= t:
                next_due += interval_sec
            ok, frame = cap.retrieve()
            if ok:
                save_still(session, frame, tag=tag, t_sec=t, source=str(video.resolve()),
                           jpeg_quality=jpeg_quality)
    finally:
        cap.release()
    return CaptureResult(session, n_read, round(n_read / fps, 3), False)


# --------------------------------------------------------------------------------------------
# Inventory — for `capture_frames.py --list`
# --------------------------------------------------------------------------------------------

def inventory() -> dict[str, Counter]:
    """``{subject: Counter(tag -> stills)}`` across every session's ``frames.csv``."""
    root = paths.frames_raw_dir()
    out: dict[str, Counter] = {}
    if not root.exists():
        return out
    for log in sorted(root.glob(f"*/*/{FRAMES_CSV}")):
        with open(log, newline="") as fh:
            for row in csv.DictReader(fh):
                out.setdefault(row["subject"], Counter())[row["tag"]] += 1
    return out
