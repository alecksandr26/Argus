"""Parallel execution: the ``spawn`` process pool, per-worker thread pinning, the atomic
per-clip committer, the three per-clip task functions, and the resumable build driver shared
by ``build_lstm_windows`` / ``build_frame_features`` / ``build_face_crops``.

Why ``spawn`` and not ``fork``: forking after MediaPipe/OpenCV have loaded inherits their
native thread pools (and any CUDA context) into the child, which is the documented cause of
``BrokenProcessPool: ... terminated abruptly`` that src/notebook/01's comments describe. Each
spawned worker is a clean interpreter that re-runs imports with the thread env already pinned.
"""

from __future__ import annotations

import glob
import json
import os
import re
import signal
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from . import checkpoint, config, paths
from .bootstrap import configure_process_threads

Key = tuple[str, str]
_LSTM_FLOAT_FMT = "%.8g"  # src/notebook/01 cell 94


# --------------------------------------------------------------------------------------------
# Atomic per-clip commit
# --------------------------------------------------------------------------------------------
class Committer:
    """Appends one clip's rows to the artifact CSV and records the clip key in the
    completed-log — both under a shared lock, both ``fsync``'d, so a kill can't leave the two
    out of sync by more than a sub-millisecond window (repaired by ``RunCheckpoint.reconcile``).
    """

    def __init__(self, csv_path: Path, completed_log: Path, lock, float_format: str | None = None):
        self.csv_path = str(csv_path)
        self.completed_log = str(completed_log)
        self.lock = lock
        self.float_format = float_format

    def commit(self, key: Key, df) -> int:
        with self.lock:
            with open(self.csv_path, "a", newline="") as fh:
                df.to_csv(fh, header=False, index=False, float_format=self.float_format)
                fh.flush()
                os.fsync(fh.fileno())
            with open(self.completed_log, "a") as fh:
                fh.write(json.dumps(list(key)) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        return len(df)


# --------------------------------------------------------------------------------------------
# Worker bootstrap + per-worker state
# --------------------------------------------------------------------------------------------
_WSTATE: dict = {}


def _worker_init(pipeline, committer: Committer) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)   # parent owns Ctrl-C; workers finish their clip
    configure_process_threads()
    try:
        import cv2
        cv2.setNumThreads(0)
    except Exception:
        pass
    _WSTATE["pipeline"] = pipeline
    _WSTATE["committer"] = committer


# --------------------------------------------------------------------------------------------
# Clip key + discovery
# --------------------------------------------------------------------------------------------
def clip_key(video_path: str) -> Key:
    return (os.path.basename(os.path.dirname(video_path)), os.path.basename(video_path))


def _parse_level(filename: str) -> int:
    m = re.search(r"level_(\d+)", filename, re.IGNORECASE)
    if not m:
        raise ValueError("Missing 'level_' prefix")
    return config.map_level(int(m.group(1)))


def discover_clips(subjects: list[str] | None, limit: int | None) -> list[str]:
    root = paths.raw_dir()
    files = sorted(glob.glob(str(root / "**" / "*.mp4"), recursive=True))
    if subjects:
        wanted = set(subjects)
        files = [f for f in files if os.path.basename(os.path.dirname(f)) in wanted]
    if limit:
        files = files[:limit]
    return files


# --------------------------------------------------------------------------------------------
# The three per-clip task functions (run in workers)
# --------------------------------------------------------------------------------------------
def lstm_task(video_path: str):
    """src/notebook/01: FaceLandmarker -> windowed, zero-pre-padded, flattened rows."""
    import numpy as np
    import pandas as pd

    from . import windowing

    key = clip_key(video_path)
    try:
        level = _parse_level(key[1])
    except ValueError as e:
        return None, (key, str(e))

    pipeline = _WSTATE["pipeline"]
    committer: Committer = _WSTATE["committer"]
    try:
        full_features, dropped = pipeline.process_video(video_path)
    except Exception as e:                       # noqa: BLE001
        return None, (key, f"process_video failed: {e!r}")
    if full_features is None:
        return None, (key, "Face detection failure (no frames)")

    meta_rows, feat_matrix = windowing.lstm_windows_for_clip(
        full_features, key[0], level, key[1], dropped
    )
    if feat_matrix is None:
        # Still mark the clip done so resume doesn't reprocess it forever.
        committer.commit(key, pd.DataFrame(columns=config.lstm_csv_columns()))
        return 0, None

    df = pd.concat(
        [pd.DataFrame(meta_rows, columns=config.LSTM_META_COLS), pd.DataFrame(feat_matrix)],
        axis=1,
    )
    return committer.commit(key, df), None


def flat_task(video_path: str):
    """src/notebook/02: FaceLandmarker -> one row per valid sampled frame."""
    import pandas as pd

    key = clip_key(video_path)
    try:
        level = _parse_level(key[1])
    except ValueError as e:
        return None, (key, str(e))

    pipeline = _WSTATE["pipeline"]
    committer: Committer = _WSTATE["committer"]
    try:
        full_features, _dropped = pipeline.process_video(video_path)
    except Exception as e:                       # noqa: BLE001
        return None, (key, f"process_video failed: {e!r}")
    if full_features is None:
        return None, (key, "Face detection failure (no frames)")

    rows = []
    for frame_idx, feat_row in enumerate(full_features):
        if feat_row[6] == 0.0:                   # ear_mar_valid — matches src/notebook/02 cell 82
            continue
        row = {"subject": key[0], "level": level, "parent_video": key[1], "frame_idx": frame_idx}
        row.update({name: float(v) for name, v in zip(config.FEATURE_COLUMN_NAMES, feat_row)})
        rows.append(row)

    df = pd.DataFrame(rows, columns=config.FLAT_CSV_COLUMNS)
    if df.empty:
        committer.commit(key, df)
        return 0, None
    return committer.commit(key, df), None


def crops_task(video_path: str):
    """src/notebook/06: BlazeFace -> cropped face JPEGs + index rows."""
    import cv2
    import pandas as pd

    key = clip_key(video_path)
    subject, filename = key
    clip_stem = os.path.splitext(filename)[0]
    try:
        level = _parse_level(filename)
    except ValueError as e:
        return None, (key, str(e))

    pipeline = _WSTATE["pipeline"]
    committer: Committer = _WSTATE["committer"]
    try:
        crops = pipeline.process_video(video_path)
    except IOError as e:
        return None, (key, str(e))
    if not crops:
        return None, (key, "No confident face detections in any sampled frame")

    crops_dir = paths.face_crops_dir()
    crops_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for frame_idx, sample_idx, crop_bgr in crops:
        name = f"{subject}_{clip_stem}_s{sample_idx}.jpg"
        image_path = crops_dir / name
        cv2.imwrite(str(image_path), crop_bgr, [cv2.IMWRITE_JPEG_QUALITY, config.CROP_JPEG_QUALITY])
        h, w = crop_bgr.shape[:2]
        rows.append({
            "subject": subject, "level": level, "parent_video": filename,
            "frame_idx": frame_idx, "sample_idx": sample_idx,
            "image_path": str(image_path), "crop_width": w, "crop_height": h,
        })
    df = pd.DataFrame(rows, columns=config.FACE_CROPS_INDEX_COLS)
    return committer.commit(key, df), None


# --------------------------------------------------------------------------------------------
# The resumable build driver
# --------------------------------------------------------------------------------------------
_TASKS = {
    "lstm_windows": (lstm_task, _LSTM_FLOAT_FMT),
    "frame_features": (flat_task, None),
    "face_crops": (crops_task, None),
}


def default_workers(per_worker_mb: int = 320) -> int:
    """RAM- and CPU-aware default. FaceLandmarker workers are ~320 MB (no TensorFlow);
    BlazeFace workers less, but this floor is safe for both."""
    ncpu = os.cpu_count() or 4
    try:
        avail_gib = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES") / (1024 ** 3)
    except (ValueError, OSError):
        avail_gib = 8.0
    ram_cap = max(1, int(0.8 * avail_gib * 1024 / per_worker_mb))
    return max(1, min(ncpu - 4, ram_cap))


def run_video_build(
    artifact: str,
    csv_path: Path,
    csv_columns: list[str],
    pipeline,
    *,
    workers: int | None = None,
    subjects: list[str] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    reset: bool = False,
    force: bool = False,
    status: bool = False,
    extra_reset_paths: tuple[Path, ...] = (),
) -> int:
    """Drive one of the three video builds with full pause/resume. Returns a process exit code."""
    task_fn, float_fmt = _TASKS[artifact]
    key_cols = ("subject", "parent_video")

    paths.ensure_dirs()
    ckpt = checkpoint.RunCheckpoint(artifact)
    all_clips = discover_clips(subjects, limit)

    if status:
        print(ckpt.status_report(len(all_clips)))
        return 0

    if reset:
        ckpt.reset((csv_path, *extra_reset_paths))
        print(f"reset: cleared {csv_path.name} and {artifact} progress"
              + (f" (+ {len(extra_reset_paths)} extra path(s))" if extra_reset_paths else ""))

    if not all_clips:
        print(f"No .mp4 clips under {paths.raw_dir()} — put your "
              f"subject_NN/level_<1-2>_clip_NN.mp4 files there first.")
        return 1

    fresh = not csv_path.exists() or csv_path.stat().st_size == 0
    if fresh:
        import pandas as pd
        pd.DataFrame(columns=csv_columns).to_csv(csv_path, index=False)
        done: set[Key] = set()
    else:
        try:
            ckpt.check_config_or_die(force)
        except checkpoint.ResumeConfigMismatch as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        removed = ckpt.reconcile(csv_path, key_cols)
        if removed:
            print(f"reconcile: dropped {removed} orphan row(s) from an interrupted commit")
        done = ckpt.completed_keys()

    todo = [c for c in all_clips if clip_key(c) not in done]
    print(f"{artifact}: {len(all_clips)} clips total, {len(done)} done, {len(todo)} to process")
    if dry_run:
        for c in todo[:20]:
            print(f"  would process {clip_key(c)[0]}/{clip_key(c)[1]}")
        if len(todo) > 20:
            print(f"  ... and {len(todo) - 20} more")
        return 0
    if not todo:
        print("Nothing to do.")
        ckpt.save_progress(total_units=len(all_clips), n_completed=len(done),
                           failed=[], started_at=ckpt.load_progress().get("started_at", time.time()))
        return 0

    n_workers = workers or default_workers()
    started_at = ckpt.load_progress().get("started_at", time.time())
    # keep failures keyed so retries don't pile up duplicate entries; a clip that later
    # succeeds is removed.
    failed: dict[Key, str] = {tuple(k): r for k, r in ckpt.load_progress().get("failed", [])}
    failed = {k: r for k, r in failed.items() if k not in done}

    stop = {"requested": False, "at": 0.0}

    def _handler(signum, _frame):
        now = time.time()
        if stop["requested"] and now - stop["at"] < 3:
            print("\nSecond interrupt — hard exit.", file=sys.stderr)
            os._exit(130)
        stop["requested"] = True
        stop["at"] = now
        print("\nPausing: letting in-flight clips finish, then saving progress "
              "(press Ctrl-C again to force-quit)...", file=sys.stderr)

    old_int = signal.signal(signal.SIGINT, _handler)
    old_term = signal.signal(signal.SIGTERM, _handler)

    import multiprocessing as mp
    from tqdm.auto import tqdm

    ctx = mp.get_context("spawn")
    manager = ctx.Manager()
    committer = Committer(csv_path, ckpt.completed_log, manager.Lock(), float_fmt)

    executor = ProcessPoolExecutor(
        max_workers=n_workers, mp_context=ctx,
        initializer=_worker_init, initargs=(pipeline, committer),
    )
    n_new = 0
    try:
        futures = {executor.submit(task_fn, c): clip_key(c) for c in todo}
        bar = tqdm(as_completed(futures), total=len(futures), desc=f"{artifact} [{n_workers}w]")
        for fut in bar:
            if stop["requested"]:
                break
            k = futures[fut]
            try:
                count, err = fut.result()
            except Exception as e:                       # noqa: BLE001
                failed[k] = f"worker crashed: {e!r}"
                continue
            if err:
                failed[err[0]] = err[1]
            elif count is not None:
                failed.pop(k, None)
                n_new += 1
            if n_new % 25 == 0:
                ckpt.save_progress(total_units=len(all_clips),
                                   n_completed=len(done) + n_new,
                                   failed=list(failed.items()), started_at=started_at)
    finally:
        executor.shutdown(wait=True, cancel_futures=stop["requested"])
        manager.shutdown()
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)

    n_done_now = len(ckpt.completed_keys())
    ckpt.save_progress(total_units=len(all_clips), n_completed=n_done_now,
                       failed=list(failed.items()), started_at=started_at)

    if stop["requested"]:
        print(f"\nPaused. {n_done_now}/{len(all_clips)} clips done. "
              f"Re-run the same command to resume.")
        return 0

    print(f"\nDone. {n_done_now}/{len(all_clips)} clips committed to {csv_path.name}.")
    if failed:
        print(f"{len(failed)} clip(s) still failing:")
        for k, reason in list(failed.items())[:20]:
            print(f"  ! {'/'.join(k)}: {reason}")
    return 0
