"""Incremental dataset updates — pick up new raw clips, drop rows for deleted ones.

The four builds are **already incremental**: each keeps ``<artifact>.completed.jsonl`` (see
:mod:`argus_dataset.checkpoint`), and re-running a ``build_*.py`` processes only clips absent
from that log and *appends* to the CSV — it never regenerates rows it already has. Adding clips
to ``raw/raw_videos/`` and re-running is the supported update path; ``--reset`` is only for a
``config.py`` change.

This module adds the two things that were missing around that engine:

  * :func:`status` — one cross-artifact view of what's **new** (raw clip not yet processed) and
    what's **orphaned** (a row/log entry whose raw ``.mp4`` was deleted or renamed), so you can
    see the state before starting a multi-hour run.
  * :func:`prune` — remove orphan rows + orphaned crop JPEGs (opt-in; :func:`status` only
    reports them).
  * :func:`run` — the same chain as ``scripts/run_all.sh``, as one entry point.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import checkpoint, cnn_lstm, config, paths, verify, windowing, workers

Key = tuple[str, str]

# (artifact, csv path fn, key columns). The three resumable "video builds".
_VIDEO_ARTIFACTS: list[tuple[str, str]] = [
    ("lstm_windows", "lstm_windows_csv"),
    ("frame_features", "frame_features_csv"),
    ("face_crops", "face_crops_index_csv"),
]
_KEY_COLS: Key = ("subject", "parent_video")


def raw_keys() -> set[Key]:
    """``(subject_dir, filename)`` for every ``.mp4`` under ``paths.raw_dir()``."""
    return {workers.clip_key(p) for p in workers.discover_clips(None, None)}


@dataclass
class ArtifactStatus:
    artifact: str
    total_raw: int
    done: int
    new: list[Key] = field(default_factory=list)
    orphan: list[Key] = field(default_factory=list)
    note: str = ""


def _video_status(raw: set[Key]) -> list[ArtifactStatus]:
    out: list[ArtifactStatus] = []
    for artifact, _csv_fn in _VIDEO_ARTIFACTS:
        done = checkpoint.RunCheckpoint(artifact).completed_keys()
        out.append(ArtifactStatus(
            artifact=artifact,
            total_raw=len(raw),
            done=len(done),
            new=sorted(raw - done),
            orphan=sorted(done - raw),
        ))
    return out


def _cnn_lstm_status(raw: set[Key]) -> ArtifactStatus:
    """cnn_lstm_windows has no RunCheckpoint: its geometry cache is keyed by crop path and its
    window CSV is always rebuilt. "New" here = crops in face_crops_index.csv without geometry
    yet; it's also stale whenever face_crops itself has unprocessed clips."""
    index_csv = paths.face_crops_index_csv()
    st = ArtifactStatus(artifact="cnn_lstm_windows", total_raw=len(raw), done=0)
    if not index_csv.exists():
        st.note = "blocked — run build_face_crops.py first"
        return st

    import json

    import pandas as pd

    geo_log = paths.cache_dir() / "cnn_lstm_windows.geo.completed.jsonl"
    geo_done = set()
    if geo_log.exists():
        geo_done = {json.loads(x) for x in geo_log.read_text().splitlines() if x.strip()}
    unique = list(dict.fromkeys(pd.read_csv(index_csv)["image_path"].tolist()))
    todo = [p for p in unique if p not in geo_done]
    st.done = len(unique) - len(todo)
    st.total_raw = len(unique)
    built = paths.cnn_lstm_windows_index_csv().exists()
    st.note = (f"{len(todo)} crop(s) need geometry; " if todo else "") + \
              ("windows CSV built" if built else "windows CSV not built yet")
    return st


def status(raw: set[Key] | None = None) -> list[ArtifactStatus]:
    raw = raw_keys() if raw is None else raw
    return _video_status(raw) + [_cnn_lstm_status(raw)]


def format_status(rows: list[ArtifactStatus]) -> str:
    lines = [
        f"raw clips on disk: {rows[0].total_raw}",
        "",
        f"  {'artifact':<20} {'done':>6} {'new':>6} {'orphan':>7}   note",
        f"  {'-' * 20} {'-' * 6} {'-' * 6} {'-' * 7}   {'-' * 30}",
    ]
    for r in rows:
        lines.append(
            f"  {r.artifact:<20} {r.done:>6} {len(r.new):>6} {len(r.orphan):>7}   {r.note}"
        )
    news = {k for r in rows for k in r.new}
    orphans = {k for r in rows for k in r.orphan}
    if news:
        lines += ["", f"new clips ({len(news)}):"]
        lines += [f"    + {s}/{v}" for s, v in sorted(news)[:20]]
        if len(news) > 20:
            lines.append(f"    ... and {len(news) - 20} more")
    if orphans:
        lines += ["", f"orphaned (raw .mp4 gone) ({len(orphans)}):"]
        lines += [f"    - {s}/{v}" for s, v in sorted(orphans)]
        lines.append("  -> run  update_dataset.py --prune  to drop these rows")
    if not news and not orphans:
        lines += ["", "dataset is up to date with the raw tree."]
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------
# prune — drop rows/log entries whose raw clip is gone
# --------------------------------------------------------------------------------------------

def prune(raw: set[Key] | None = None) -> dict[str, tuple[int, int]]:
    """For each video artifact, drop completed-log keys + CSV rows whose ``.mp4`` no longer
    exists, and delete the now-orphaned ``face_crops/*.jpg``. Returns
    ``{artifact: (log_lines_removed, csv_rows_removed)}``. Re-run ``build_cnn_lstm_windows.py``
    afterwards — its windowing re-reads the trimmed ``face_crops_index.csv``."""
    raw = raw_keys() if raw is None else raw
    result: dict[str, tuple[int, int]] = {}

    for artifact, csv_fn in _VIDEO_ARTIFACTS:
        csv_path = getattr(paths, csv_fn)()
        ckpt = checkpoint.RunCheckpoint(artifact)
        if artifact == "face_crops":
            _delete_orphan_crops(csv_path, raw)
        result[artifact] = ckpt.prune_missing(csv_path, _KEY_COLS, raw)

    return result


def _delete_orphan_crops(index_csv: Path, raw: set[Key]) -> int:
    if not index_csv.exists() or index_csv.stat().st_size == 0:
        return 0
    import pandas as pd

    df = pd.read_csv(index_csv)
    if df.empty:
        return 0
    gone = df[~df[list(_KEY_COLS)].apply(lambda r: (r.iloc[0], r.iloc[1]) in raw, axis=1)]
    n = 0
    for p in gone["image_path"]:
        fp = Path(p)
        if fp.exists():
            fp.unlink()
            n += 1
    return n


# --------------------------------------------------------------------------------------------
# run — the incremental build chain (same as scripts/run_all.sh)
# --------------------------------------------------------------------------------------------

def run(*, workers_n: int | None = None, subjects: list[str] | None = None,
        enrich: bool = False) -> int:
    """Run every build that has new work, in dependency order. Each build is already
    resumable/incremental, so this is safe to re-run and cheap when nothing changed."""
    from . import assets, pipelines

    assets.ensure_face_landmarker()
    assets.ensure_face_detector()
    landmarker = pipelines.FaceLandmarkerFeatureExtractor(paths.face_landmarker_path())

    specs = [
        ("lstm_windows", paths.lstm_windows_csv(), config.lstm_csv_columns(), landmarker, ()),
        ("frame_features", paths.frame_features_csv(), config.FLAT_CSV_COLUMNS, landmarker, ()),
        ("face_crops", paths.face_crops_index_csv(), config.FACE_CROPS_INDEX_COLS,
         pipelines.FaceCropExtractor(paths.face_detector_path()), (paths.face_crops_dir(),)),
    ]
    for artifact, csv_path, cols, pipeline, extra_reset in specs:
        print(f"\n=== {artifact} ===")
        code = workers.run_video_build(
            artifact, csv_path, cols, pipeline,
            workers=workers_n, subjects=subjects, extra_reset_paths=extra_reset,
        )
        if code != 0:
            return code

    if enrich and paths.frame_features_csv().exists():
        import pandas as pd
        df = pd.read_csv(paths.frame_features_csv())
        out = paths.frame_features_enriched_csv()
        windowing.enrich_frame_features(df).to_csv(out, index=False)
        print(f"\nenriched -> {out.name}")

    print("\n=== cnn_lstm_windows ===")
    code = cnn_lstm.run(workers_n=workers_n)
    if code != 0:
        return code

    print()
    verify.check_all()
    return 0
