"""Pure windowing / enrichment maths — no I/O, no MediaPipe.

  * :func:`lstm_windows_for_clip`     — src/notebook/01 cell 94's window loop.
  * :func:`contiguous_runs` / :func:`cnn_lstm_windows_for_clip` — src/notebook/09 cell 14.
  * :func:`enrich_frame_features`     — src/notebook/02 cells 106-114.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config

_EAR_MAR_VALID_COL = 6  # index of ear_mar_valid within a 58-wide frame feature row


# --- LSTM windowed dataset (src/notebook/01 cell 94) --------------------------------------

def lstm_windows_for_clip(
    full_features: np.ndarray,
    subject: str,
    level: int,
    parent_video: str,
    dropped_frames: int,
) -> tuple[list[tuple], np.ndarray | None]:
    """Slide every ``config.LSTM_WINDOW_CONFIGS`` window over one clip's ``(n, 58)`` feature
    matrix. Drop any window containing a frame with ``ear_mar_valid == 0``. Zero-**pre**-pad the
    survivors to ``config.LSTM_MAX_TIMESTEPS`` and flatten timestep-outer / feature-inner.

    Returns ``(meta_rows, feature_matrix)`` where ``meta_rows[i]`` lines up with
    ``feature_matrix[i]`` (a ``float32`` row of ``MAX_TIMESTEPS * NUM_FEATURES`` values), or
    ``(meta_rows, None)`` when no window survived.
    """
    stride = int(config.LSTM_STRIDE_SEC * config.SAMPLING_FPS)
    n = len(full_features)
    meta_rows: list[tuple] = []
    feat_rows: list[np.ndarray] = []

    for win_sec in config.LSTM_WINDOW_CONFIGS:
        win_size = int(win_sec * config.SAMPLING_FPS)
        pad_amount = config.LSTM_MAX_TIMESTEPS - win_size
        if pad_amount < 0:
            raise ValueError(
                f"window of {win_size} frames exceeds LSTM_MAX_TIMESTEPS="
                f"{config.LSTM_MAX_TIMESTEPS}"
            )
        for start in range(0, n - win_size + 1, stride):
            window = full_features[start:start + win_size]
            if np.any(window[:, _EAR_MAR_VALID_COL] == 0.0):
                continue
            padded = np.pad(window, ((pad_amount, 0), (0, 0)), mode="constant")
            meta_rows.append(
                (subject, level, parent_video, win_sec, win_size, dropped_frames)
            )
            feat_rows.append(padded.astype(np.float32).reshape(-1))

    if not feat_rows:
        return meta_rows, None
    return meta_rows, np.stack(feat_rows)


# --- CNN+LSTM windowed image-sequence index (src/notebook/09 cell 14) ---------------------

def contiguous_runs(sorted_indices: list[int]) -> list[tuple[int, int]]:
    """``[0,1,2,5,6,9] -> [(0,2),(5,6),(9,9)]``. A gap in ``sample_idx`` (a frame with no
    confident detection) starts a new run."""
    runs: list[tuple[int, int]] = []
    run_start = prev = sorted_indices[0]
    for idx in sorted_indices[1:]:
        if idx != prev + 1:
            runs.append((run_start, prev))
            run_start = idx
        prev = idx
    runs.append((run_start, prev))
    return runs


def cnn_lstm_windows_for_clip(
    clip_df: pd.DataFrame,
    geo_features_by_path: dict[str, list[float]],
) -> list[dict]:
    """``clip_df``: the ``face_crops_index`` rows for one ``(subject, parent_video)``. Tiles
    non-overlapping windows (``stride == win_size``) per maximal contiguous ``sample_idx`` run,
    for each ``config.CNNLSTM_WINDOW_CONFIGS`` duration. Mirrors src/notebook/09's
    ``build_windows_for_clip`` including the ``geometric_feature_seq`` string format.
    """
    sample_idx_to_path = dict(zip(clip_df["sample_idx"], clip_df["image_path"]))
    available = sorted(sample_idx_to_path.keys())
    if not available:
        return []

    runs = contiguous_runs(available)
    windows: list[dict] = []
    for win_sec in config.CNNLSTM_WINDOW_CONFIGS:
        win_size = int(win_sec * config.SAMPLING_FPS)
        for run_start, run_end in runs:
            for start in range(run_start, run_end - win_size + 2, win_size):
                end = start + win_size  # exclusive
                image_paths = [sample_idx_to_path[si] for si in range(start, end)]
                geo_seq = ";".join(
                    ",".join(f"{v:.6f}" for v in geo_features_by_path[p])
                    for p in image_paths
                )
                windows.append({
                    "window_duration_sec": win_sec,
                    "n_real_frames": win_size,
                    "start_sample_idx": start,
                    "end_sample_idx": end - 1,
                    "image_paths": ";".join(image_paths),
                    "geometric_feature_seq": geo_seq,
                })
    return windows


# --- flat-dataset temporal enrichment (src/notebook/02 cells 106-114) --------------------

def _rolling(df: pd.DataFrame, group_cols, feature_cols, window: int, stat: str) -> pd.DataFrame:
    """Causal (backward-only) rolling stat per group, realigned to ``df``'s row order."""
    g = df.groupby(group_cols, sort=False)[feature_cols]
    result = (g.rolling(window=window, min_periods=1).mean() if stat == "mean"
              else g.rolling(window=window, min_periods=1).std())
    return result.reset_index(level=list(range(len(group_cols))), drop=True).sort_index()


def enrich_frame_features(df_frame_features: pd.DataFrame) -> pd.DataFrame:
    """``frame_features.csv`` -> ``frame_features_enriched.csv`` contents (158 columns).

    Adds ``EAR_mean``, then per ``config.ROLLING_BASE_COLS`` column: rolling mean/std over the
    short and long windows, plus a first-difference ``_delta1``. Rows are sorted by
    ``(subject, parent_video, frame_idx)`` so the rolling/diff ops are temporally meaningful.
    """
    df = df_frame_features.copy()
    df["EAR_mean"] = (df["EAR_left"] + df["EAR_right"]) / 2
    df = df.sort_values(["subject", "parent_video", "frame_idx"]).reset_index(drop=True)

    group_cols = ["subject", "parent_video"]
    base = config.ROLLING_BASE_COLS
    df_enriched = df.copy()

    for window, tag in ((config.ROLL_WINDOW_SHORT, f"{config.ROLL_WINDOW_SHORT}f"),
                        (config.ROLL_WINDOW_LONG, f"{config.ROLL_WINDOW_LONG}f")):
        roll_mean = _rolling(df, group_cols, base, window, "mean")
        roll_mean.columns = [f"{c}_rollmean_{tag}" for c in base]
        roll_std = _rolling(df, group_cols, base, window, "std").fillna(0.0)
        roll_std.columns = [f"{c}_rollstd_{tag}" for c in base]
        df_enriched = pd.concat([df_enriched, roll_mean, roll_std], axis=1)

    delta = df.groupby(group_cols, sort=False)[base].diff().fillna(0.0)
    delta.columns = [f"{c}_delta1" for c in base]
    df_enriched = pd.concat([df_enriched, delta], axis=1)
    return df_enriched
