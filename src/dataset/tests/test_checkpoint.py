"""RunCheckpoint: completed-log tracking, orphan-row reconciliation, config-hash resume guard."""

import importlib
import json

import pandas as pd
import pytest


@pytest.fixture()
def ck(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_DATASET_ROOT", str(tmp_path))
    from argus_dataset import paths as _paths
    importlib.reload(_paths)
    from argus_dataset import checkpoint as _ckpt
    importlib.reload(_ckpt)
    _paths.ensure_dirs()
    return _ckpt, _paths


def test_completed_log_roundtrip(ck):
    checkpoint, _paths = ck
    c = checkpoint.RunCheckpoint("lstm_windows")
    c.append_completed(("subject_07", "level_1_clip_01.mp4"))
    c.append_completed(("subject_07", "level_2_clip_01.mp4"))
    assert c.completed_keys() == {
        ("subject_07", "level_1_clip_01.mp4"),
        ("subject_07", "level_2_clip_01.mp4"),
    }


def test_reconcile_drops_orphan_rows(ck):
    checkpoint, paths = ck
    c = checkpoint.RunCheckpoint("frame_features")
    csv = paths.frame_features_csv()

    # committed clip + an orphan clip whose completed-log line never landed
    pd.DataFrame([
        {"subject": "s1", "parent_video": "level_1_clip_01.mp4", "frame_idx": 0},
        {"subject": "s1", "parent_video": "level_1_clip_01.mp4", "frame_idx": 1},
        {"subject": "s2", "parent_video": "level_2_clip_01.mp4", "frame_idx": 0},  # orphan
    ]).to_csv(csv, index=False)
    c.append_completed(("s1", "level_1_clip_01.mp4"))

    removed = c.reconcile(csv, ("subject", "parent_video"))
    assert removed == 1
    df = pd.read_csv(csv)
    assert set(df["subject"]) == {"s1"}
    assert len(df) == 2


def test_config_hash_guard(ck):
    checkpoint, _paths = ck
    c = checkpoint.RunCheckpoint("face_crops")
    c.save_progress(total_units=10, n_completed=1, failed=[], started_at=0.0)
    c.check_config_or_die(force=False)  # same config -> fine

    # simulate a config change by rewriting the stored hash
    p = c.progress_path
    data = json.loads(p.read_text())
    data["config_hash"] = "deadbeefdeadbeef"
    p.write_text(json.dumps(data))

    with pytest.raises(checkpoint.ResumeConfigMismatch):
        c.check_config_or_die(force=False)
    c.check_config_or_die(force=True)   # --force overrides


def test_reset_clears_everything(ck):
    checkpoint, paths = ck
    c = checkpoint.RunCheckpoint("lstm_windows")
    csv = paths.lstm_windows_csv()
    csv.write_text("subject,level\n")
    c.append_completed(("s", "level_1_clip_01.mp4"))
    c.save_progress(total_units=1, n_completed=1, failed=[], started_at=0.0)

    c.reset((csv,))
    assert not csv.exists()
    assert not c.completed_log.exists()
    assert not c.progress_path.exists()
