"""LSTM + CNN-LSTM windowing and flat-dataset enrichment."""

import numpy as np
import pandas as pd
import pytest

from argus_dataset import config, windowing


# --- LSTM windowing (src/notebook/01 cell 94) --------------------------------------------

def _valid_feature_matrix(n_frames):
    m = np.random.default_rng(0).random((n_frames, config.NUM_FEATURES)).astype(np.float32)
    m[:, 6] = 1.0  # ear_mar_valid — all frames valid
    return m


def test_lstm_windows_shapes_padding_and_meta():
    n = 60  # 12 s at 5 fps
    meta, feats = windowing.lstm_windows_for_clip(
        _valid_feature_matrix(n), "subject_07", 2, "level_2_clip_01.mp4", 3
    )
    assert feats is not None
    assert feats.shape[1] == config.LSTM_MAX_TIMESTEPS * config.NUM_FEATURES
    assert len(meta) == feats.shape[0]

    for (subj, lvl, pv, dur, nreal, dropped), row in zip(meta, feats):
        assert (subj, lvl, pv, dropped) == ("subject_07", 2, "level_2_clip_01.mp4", 3)
        assert nreal == int(dur * config.SAMPLING_FPS)
        grid = row.reshape(config.LSTM_MAX_TIMESTEPS, config.NUM_FEATURES)
        pad = config.LSTM_MAX_TIMESTEPS - nreal
        assert np.all(grid[:pad] == 0.0)          # zero-PRE-pad
        assert np.any(grid[pad:] != 0.0)


def test_lstm_windows_skip_invalid_pose_frame():
    m = _valid_feature_matrix(30)
    m[10, 6] = 0.0  # one invalid frame kills every window covering frame 10
    meta, feats = windowing.lstm_windows_for_clip(m, "s", 1, "level_1_clip_01.mp4", 0)
    # a 1s (5-frame) window starting at 0 or 5 survives; one covering frame 10 does not
    durations = {d for (_, _, _, d, _, _) in meta}
    assert 1.0 in durations
    assert feats is not None


def test_lstm_windows_all_invalid_returns_none_matrix():
    m = _valid_feature_matrix(30)
    m[:, 6] = 0.0
    meta, feats = windowing.lstm_windows_for_clip(m, "s", 1, "level_1_clip_01.mp4", 30)
    assert feats is None and meta == []


# --- CNN-LSTM windowing (src/notebook/09 cell 14) ----------------------------------------

def test_contiguous_runs():
    assert windowing.contiguous_runs([0, 1, 2, 5, 6, 9]) == [(0, 2), (5, 6), (9, 9)]
    assert windowing.contiguous_runs([4]) == [(4, 4)]


def test_cnn_lstm_windows_tiling_and_geo_seq():
    # 40 contiguous samples -> a 3s (15-frame) config yields 2 non-overlapping windows
    rows = [{"sample_idx": i, "image_path": f"/x/s{i}.jpg", "level": 2} for i in range(40)]
    clip_df = pd.DataFrame(rows)
    geo = {r["image_path"]: [float(j) for j in range(config.NUM_GEO_FEATURES)] for r in rows}

    wins = windowing.cnn_lstm_windows_for_clip(clip_df, geo)
    three_s = [w for w in wins if w["window_duration_sec"] == 3.0]
    assert len(three_s) == 40 // 15  # == 2, stride == window size

    w = three_s[0]
    assert w["n_real_frames"] == 15
    assert len(w["image_paths"].split(";")) == 15
    frames = w["geometric_feature_seq"].split(";")
    assert len(frames) == 15
    assert frames[0].split(",") == [f"{float(j):.6f}" for j in range(config.NUM_GEO_FEATURES)]


def test_cnn_lstm_windows_split_on_gap():
    rows = [{"sample_idx": i, "image_path": f"/x/s{i}.jpg", "level": 1}
            for i in list(range(10)) + list(range(20, 30))]  # gap 10..19
    clip_df = pd.DataFrame(rows)
    geo = {r["image_path"]: [0.0] * config.NUM_GEO_FEATURES for r in rows}
    wins = windowing.cnn_lstm_windows_for_clip(clip_df, geo)
    # no window may straddle the gap
    for w in wins:
        assert w["start_sample_idx"] // 20 == w["end_sample_idx"] // 20


def test_cnn_lstm_windows_minority_overlap_yields_more_windows():
    # 40 contiguous samples, 3s (15-frame) config.
    #   overlap 0.0 -> stride 15 -> starts {0, 15}                 -> 2 windows
    #   overlap 0.5 -> stride  8 -> starts {0, 8, 16, 24}          -> 4 windows
    rows = [{"sample_idx": i, "image_path": f"/x/s{i}.jpg", "level": 2} for i in range(40)]
    clip_df = pd.DataFrame(rows)
    geo = {r["image_path"]: [0.0] * config.NUM_GEO_FEATURES for r in rows}

    base = [w for w in windowing.cnn_lstm_windows_for_clip(clip_df, geo, window_overlap=0.0)
            if w["window_duration_sec"] == 3.0]
    over = [w for w in windowing.cnn_lstm_windows_for_clip(clip_df, geo, window_overlap=0.5)
            if w["window_duration_sec"] == 3.0]

    assert [w["start_sample_idx"] for w in base] == [0, 15]
    assert [w["start_sample_idx"] for w in over] == [0, 8, 16, 24]
    # every overlapped window is still a full, in-bounds, gap-free window
    for w in over:
        assert w["n_real_frames"] == 15
        assert w["end_sample_idx"] - w["start_sample_idx"] == 14
        assert w["end_sample_idx"] <= 39
        assert len(w["image_paths"].split(";")) == 15
    # default is unchanged (non-overlapping)
    default = [w for w in windowing.cnn_lstm_windows_for_clip(clip_df, geo)
               if w["window_duration_sec"] == 3.0]
    assert [w["start_sample_idx"] for w in default] == [0, 15]


def test_cnn_lstm_windows_overlap_never_straddles_gap():
    rows = [{"sample_idx": i, "image_path": f"/x/s{i}.jpg", "level": 2}
            for i in list(range(30)) + list(range(50, 80))]  # gap 30..49
    clip_df = pd.DataFrame(rows)
    geo = {r["image_path"]: [0.0] * config.NUM_GEO_FEATURES for r in rows}
    wins = windowing.cnn_lstm_windows_for_clip(clip_df, geo, window_overlap=0.5)
    for w in wins:
        assert (w["start_sample_idx"] < 30) == (w["end_sample_idx"] < 30)


def test_cnn_lstm_windows_overlap_out_of_range_rejected():
    clip_df = pd.DataFrame([{"sample_idx": 0, "image_path": "/x/s0.jpg", "level": 2}])
    with pytest.raises(ValueError):
        windowing.cnn_lstm_windows_for_clip(clip_df, {}, window_overlap=1.0)


# --- enrichment (src/notebook/02 cells 106-114) -----------------------------------------

def test_enrich_frame_features_columns_and_causality():
    base_cols = config.FLAT_CSV_COLUMNS
    n = 12
    data = {c: np.linspace(0, 1, n) for c in base_cols}
    data["subject"] = ["s"] * n
    data["parent_video"] = ["level_1_clip_01.mp4"] * n
    data["frame_idx"] = list(range(n))
    data["level"] = [1] * n
    df = pd.DataFrame(data)[base_cols]

    out = windowing.enrich_frame_features(df)
    n_base = len(config.ROLLING_BASE_COLS)
    assert out.shape[1] == len(base_cols) + 1 + n_base * 4 + n_base
    assert "EAR_mean" in out.columns
    # first row's deltas are 0 (no prior frame)
    assert (out.iloc[0][[f"{c}_delta1" for c in config.ROLLING_BASE_COLS]] == 0.0).all()
    # rollmean_5f at row 4 == mean of rows 0..4 for a monotone column
    col = "MAR"
    assert np.isclose(out[f"{col}_rollmean_5f"].iloc[4], df[col].iloc[0:5].mean())
