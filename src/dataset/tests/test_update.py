"""update.py — new-vs-orphan classification and orphan pruning."""

import importlib
import json

import pandas as pd
import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_DATASET_ROOT", str(tmp_path))
    from argus_dataset import paths
    importlib.reload(paths)
    from argus_dataset import checkpoint, config, workers, update
    for m in (checkpoint, workers, update):
        importlib.reload(m)
    paths.ensure_dirs()
    return paths, checkpoint, config, update


def _raw_clip(paths, subject, name):
    d = paths.raw_dir() / subject
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(b"\x00")


def test_status_classifies_new_and_orphan(env):
    paths, checkpoint, config, update = env

    # raw tree: subject_01 has clip_01 (processed) + clip_02 (new)
    _raw_clip(paths, "subject_01", "level_1_clip_01.mp4")
    _raw_clip(paths, "subject_01", "level_1_clip_02.mp4")

    csv = paths.frame_features_csv()
    pd.DataFrame(
        [{"subject": "subject_01", "level": 1, "parent_video": "level_1_clip_01.mp4", "frame_idx": 0},
         {"subject": "subject_09", "level": 2, "parent_video": "level_2_clip_01.mp4", "frame_idx": 0}],
    ).to_csv(csv, index=False)
    ck = checkpoint.RunCheckpoint("frame_features")
    ck.append_completed(("subject_01", "level_1_clip_01.mp4"))
    ck.append_completed(("subject_09", "level_2_clip_01.mp4"))  # raw .mp4 gone -> orphan

    rows = {r.artifact: r for r in update.status()}
    ff = rows["frame_features"]
    assert ff.new == [("subject_01", "level_1_clip_02.mp4")]
    assert ff.orphan == [("subject_09", "level_2_clip_01.mp4")]
    # lstm_windows never ran -> both raw clips are "new", nothing orphaned
    assert set(rows["lstm_windows"].new) == {
        ("subject_01", "level_1_clip_01.mp4"), ("subject_01", "level_1_clip_02.mp4"),
    }
    assert rows["lstm_windows"].orphan == []


def test_prune_drops_only_orphans(env):
    paths, checkpoint, config, update = env
    _raw_clip(paths, "subject_01", "level_1_clip_01.mp4")

    csv = paths.frame_features_csv()
    pd.DataFrame([
        {"subject": "subject_01", "level": 1, "parent_video": "level_1_clip_01.mp4", "frame_idx": 0},
        {"subject": "subject_01", "level": 1, "parent_video": "level_1_clip_01.mp4", "frame_idx": 1},
        {"subject": "subject_09", "level": 2, "parent_video": "level_2_clip_01.mp4", "frame_idx": 0},
    ]).to_csv(csv, index=False)
    ck = checkpoint.RunCheckpoint("frame_features")
    ck.append_completed(("subject_01", "level_1_clip_01.mp4"))
    ck.append_completed(("subject_09", "level_2_clip_01.mp4"))

    res = update.prune()
    assert res["frame_features"] == (1, 1)  # 1 log line, 1 csv row

    df = pd.read_csv(csv)
    assert set(df["subject"]) == {"subject_01"}
    assert len(df) == 2
    assert checkpoint.RunCheckpoint("frame_features").completed_keys() == {
        ("subject_01", "level_1_clip_01.mp4"),
    }


def test_prune_missing_noop_when_all_present(env):
    paths, checkpoint, config, update = env
    _raw_clip(paths, "subject_01", "level_1_clip_01.mp4")
    csv = paths.lstm_windows_csv()
    cols = config.lstm_csv_columns()
    row = {c: 0 for c in cols}
    row.update(subject="subject_01", level=1, parent_video="level_1_clip_01.mp4")
    pd.DataFrame([row], columns=cols).to_csv(csv, index=False)
    ck = checkpoint.RunCheckpoint("lstm_windows")
    ck.append_completed(("subject_01", "level_1_clip_01.mp4"))

    logs, rows = ck.prune_missing(csv, ("subject", "parent_video"), update.raw_keys())
    assert (logs, rows) == (0, 0)
    assert len(pd.read_csv(csv)) == 1
