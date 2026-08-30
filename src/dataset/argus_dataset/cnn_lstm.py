"""``build_cnn_lstm_windows`` — port of ``src/notebook/09_dataset_creation_cnn_lstm.ipynb``.

Two steps:

  1. **Per-crop geometry** (the slow part — one FaceLandmarker IMAGE inference per unique
     crop). Parallelised across a ``spawn`` pool and streamed to
     ``processed/.cache/geo_per_crop.parquet`` with a companion ``completed.jsonl`` of paths,
     so it resumes after an interrupt without redoing finished crops.
  2. **Windowing** — fast, pure (``windowing.cnn_lstm_windows_for_clip``); always re-run over
     the full cache and rewrite ``cnn_lstm_windows_index.csv``.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import signal
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

import pandas as pd

from . import config, paths, windowing, workers

_ARTIFACT = "cnn_lstm_windows"
_CACHE_COLS = ["image_path", *config.GEO_FEATURE_NAMES]


# --- step 1: per-crop geometry ----------------------------------------------------------

_LM = {}


def _geo_worker_init(model_path: str, lock):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    workers.configure_process_threads()
    try:
        import cv2
        cv2.setNumThreads(0)
    except Exception:
        pass
    from . import pipelines
    _LM["landmarker"] = pipelines.make_image_landmarker(model_path)
    _LM["lock"] = lock
    _LM["cache"] = str(paths.geo_per_crop_cache())
    _LM["log"] = str(paths.cache_dir() / f"{_ARTIFACT}.geo.completed.jsonl")


def _geo_worker(image_path: str):
    from . import pipelines
    try:
        vals = pipelines.extract_geo_for_crop(image_path, _LM["landmarker"])
    except Exception as e:                       # noqa: BLE001
        return image_path, None, repr(e)
    row = pd.DataFrame([[image_path, *vals]], columns=_CACHE_COLS)
    with _LM["lock"]:
        cache = _LM["cache"]
        write_header = not os.path.exists(cache) or os.path.getsize(cache) == 0
        # parquet can't append; use CSV alongside during the run, convert at the end.
        csv_cache = cache + ".partial.csv"
        with open(csv_cache, "a", newline="") as fh:
            row.to_csv(fh, header=(not os.path.exists(csv_cache) or os.path.getsize(csv_cache) == 0),
                       index=False)
            fh.flush(); os.fsync(fh.fileno())
        with open(_LM["log"], "a") as fh:
            fh.write(json.dumps(image_path) + "\n"); fh.flush(); os.fsync(fh.fileno())
    return image_path, vals, None


def _load_geo_cache() -> dict[str, list[float]]:
    partial = str(paths.geo_per_crop_cache()) + ".partial.csv"
    parquet = paths.geo_per_crop_cache()
    frames = []
    if parquet.exists():
        try:
            frames.append(pd.read_parquet(parquet))
        except Exception:
            frames.append(pd.read_csv(parquet))
    if os.path.exists(partial):
        frames.append(pd.read_csv(partial))
    if not frames:
        return {}
    df = pd.concat(frames, ignore_index=True).drop_duplicates("image_path", keep="last")
    return {r.image_path: [getattr(r, n) for n in config.GEO_FEATURE_NAMES]
            for r in df.itertuples(index=False)}


def _finalize_geo_cache() -> None:
    """Fold the append-only partial CSV into the parquet cache and drop the partial."""
    geo = _load_geo_cache()
    if not geo:
        return
    df = pd.DataFrame(
        [[p, *vals] for p, vals in geo.items()], columns=_CACHE_COLS
    )
    try:
        df.to_parquet(paths.geo_per_crop_cache(), index=False)
        partial = str(paths.geo_per_crop_cache()) + ".partial.csv"
        if os.path.exists(partial):
            os.remove(partial)
    except Exception:
        df.to_csv(paths.geo_per_crop_cache(), index=False)


# --- driver ---------------------------------------------------------------------------

def run(*, workers_n: int | None = None, reset: bool = False, status: bool = False,
        force: bool = False, dry_run: bool = False) -> int:
    paths.ensure_dirs()
    index_csv = paths.face_crops_index_csv()
    if not index_csv.exists():
        print(f"{index_csv} not found — run build_face_crops.py first.", file=sys.stderr)
        return 1

    geo_log = paths.cache_dir() / f"{_ARTIFACT}.geo.completed.jsonl"
    out_csv = paths.cnn_lstm_windows_index_csv()

    if reset:
        for p in (paths.geo_per_crop_cache(),
                  paths.cache_dir() / (paths.geo_per_crop_cache().name + ".partial.csv"),
                  geo_log, out_csv):
            if p.exists():
                p.unlink()
        print("reset: cleared geo cache + cnn_lstm_windows_index.csv")

    df_crops = pd.read_csv(index_csv)
    df_crops = df_crops.sort_values(["subject", "parent_video", "sample_idx"]).reset_index(drop=True)
    levels = sorted(int(x) for x in df_crops["level"].unique())
    if levels != list(range(1, config.NUM_CLASSES + 1)):
        print(f"face_crops_index.csv has levels {levels}, expected {list(range(1, config.NUM_CLASSES + 1))} "
              f"— it's stale relative to the binary scheme. Re-run build_face_crops.py.", file=sys.stderr)
        return 2

    unique_paths = list(dict.fromkeys(df_crops["image_path"].tolist()))
    done = set()
    if geo_log.exists():
        done = {json.loads(l) for l in geo_log.read_text().splitlines() if l.strip()}
    todo = [p for p in unique_paths if p not in done]

    if status:
        print(f"{_ARTIFACT}: {len(unique_paths)} unique crops, {len(done)} geo-extracted, "
              f"{len(todo)} to go. windows csv: {'built' if out_csv.exists() else 'not built'}")
        return 0

    print(f"step 1/2: geometry for {len(todo)}/{len(unique_paths)} crops")
    if dry_run:
        print("  (dry run — stopping)")
        return 0

    if todo:
        _run_geo_pool(todo, workers_n)
    _finalize_geo_cache()

    print("step 2/2: building windows")
    geo_by_path = _load_geo_cache()
    missing = [p for p in unique_paths if p not in geo_by_path]
    if missing:
        print(f"  {len(missing)} crops still missing geometry — re-run to finish step 1.", file=sys.stderr)
        return 3

    rows, skipped = [], 0
    for (subject, parent_video), clip_df in df_crops.groupby(["subject", "parent_video"], sort=False):
        wins = windowing.cnn_lstm_windows_for_clip(clip_df, geo_by_path)
        if not wins:
            skipped += 1
            continue
        for w in wins:
            w.update(subject=subject, parent_video=parent_video, level=int(clip_df["level"].iloc[0]))
            rows.append(w)

    df_out = pd.DataFrame(rows, columns=config.CNNLSTM_INDEX_COLS)
    df_out.to_csv(out_csv, index=False)
    print(f"\nDone. {len(df_out)} windows across "
          f"{df_crops.groupby(['subject', 'parent_video']).ngroups - skipped} clips "
          f"-> {out_csv.name}  ({skipped} clips had no gap-free window)")
    return 0


def _run_geo_pool(todo: list[str], workers_n: int | None) -> None:
    from tqdm.auto import tqdm

    n = workers_n or workers.default_workers(per_worker_mb=320)
    ctx = mp.get_context("spawn")
    manager = ctx.Manager()
    model_path = str(paths.face_landmarker_path())

    stop = {"requested": False}

    def _handler(signum, _frame):
        stop["requested"] = True
        print("\nPausing after in-flight crops...", file=sys.stderr)

    old = signal.signal(signal.SIGINT, _handler)
    ex = ProcessPoolExecutor(max_workers=n, mp_context=ctx,
                             initializer=_geo_worker_init, initargs=(model_path, manager.Lock()))
    try:
        futures = [ex.submit(_geo_worker, p) for p in todo]
        for _ in tqdm(as_completed(futures), total=len(futures), desc=f"geo [{n}w]"):
            if stop["requested"]:
                break
    finally:
        ex.shutdown(wait=True, cancel_futures=stop["requested"])
        manager.shutdown()
        signal.signal(signal.SIGINT, old)

    if stop["requested"]:
        print("Paused — re-run build_cnn_lstm_windows.py to resume.", file=sys.stderr)
        sys.exit(0)
