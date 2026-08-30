"""Committer: headerless append onto a parent-written header, plus completed-log fsync."""

import threading

import pandas as pd
import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_DATASET_ROOT", str(tmp_path))
    import importlib
    from argus_dataset import paths
    importlib.reload(paths)
    from argus_dataset import checkpoint, config, workers
    importlib.reload(checkpoint)
    importlib.reload(workers)
    paths.ensure_dirs()
    return paths, checkpoint, config, workers


def test_commit_appends_and_logs(env):
    paths, checkpoint, config, workers = env
    csv = paths.frame_features_csv()
    pd.DataFrame(columns=config.FLAT_CSV_COLUMNS).to_csv(csv, index=False)  # parent header

    ckpt = checkpoint.RunCheckpoint("frame_features")
    committer = workers.Committer(csv, ckpt.completed_log, threading.Lock())

    def _row(subject, pv, fi):
        r = {c: 0.0 for c in config.FLAT_CSV_COLUMNS}
        r.update(subject=subject, level=1, parent_video=pv, frame_idx=fi, ear_mar_valid=1.0)
        return r

    committer.commit(("s1", "level_1_clip_01.mp4"),
                     pd.DataFrame([_row("s1", "level_1_clip_01.mp4", i) for i in range(3)],
                                  columns=config.FLAT_CSV_COLUMNS))
    committer.commit(("s1", "level_2_clip_01.mp4"),
                     pd.DataFrame([_row("s1", "level_2_clip_01.mp4", i) for i in range(2)],
                                  columns=config.FLAT_CSV_COLUMNS))

    df = pd.read_csv(csv)
    assert list(df.columns) == config.FLAT_CSV_COLUMNS
    assert len(df) == 5
    assert set(df["parent_video"]) == {"level_1_clip_01.mp4", "level_2_clip_01.mp4"}

    assert checkpoint.RunCheckpoint("frame_features").completed_keys() == {
        ("s1", "level_1_clip_01.mp4"), ("s1", "level_2_clip_01.mp4"),
    }


def test_reconcile_after_orphan_commit(env):
    """A commit whose CSV append landed but whose completed-log line didn't -> reconcile drops it."""
    paths, checkpoint, config, workers = env
    csv = paths.lstm_windows_csv()
    cols = config.lstm_csv_columns()
    pd.DataFrame(columns=cols).to_csv(csv, index=False)
    ckpt = checkpoint.RunCheckpoint("lstm_windows")

    good = {c: 0 for c in cols}
    good.update(subject="s1", level=1, parent_video="level_1_clip_01.mp4",
                window_duration_sec=1.0, n_real_frames=5, dropped_frames_in_video=0)
    orphan = dict(good, subject="s2", parent_video="level_2_clip_01.mp4")
    pd.DataFrame([good, orphan], columns=cols).to_csv(csv, mode="a", header=False, index=False)
    ckpt.append_completed(("s1", "level_1_clip_01.mp4"))  # only the good one logged

    removed = ckpt.reconcile(csv, ("subject", "parent_video"))
    assert removed == 1
    assert set(pd.read_csv(csv)["subject"]) == {"s1"}
