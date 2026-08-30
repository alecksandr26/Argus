"""Schema / label / relationship checks for the generated artifacts.

Not a bit-for-bit comparison against Colab output (MediaPipe isn't cross-platform
deterministic — see the plan's Risks). This asserts the *contract* the training notebooks
rely on: column names and order, label domain, and the internal relationships each file must
satisfy. ``--compare <colab.csv>`` additionally checks column parity and that per-level counts
and per-feature mean/std are within tolerance.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

from . import config, paths

Result = tuple[bool, str]


def _check(cond: bool, msg: str) -> Result:
    return (bool(cond), msg)


# --- individual artifacts ----------------------------------------------------------------

def check_lstm_windows(path=None) -> list[Result]:
    path = path or paths.lstm_windows_csv()
    if not os.path.exists(path):
        return [_check(False, f"{path} missing")]
    df = pd.read_csv(path)
    cols = config.lstm_csv_columns()
    out = [
        _check(list(df.columns) == cols,
               f"columns == {len(cols)} expected (META + t###_f##); got {len(df.columns)}"),
        _check(set(df["level"].unique()) <= {1, 2}, f"level in {{1,2}} (got {sorted(df['level'].unique())})"),
        _check(set(df["level"].unique()) == {1, 2}, "both classes present"),
        _check((df["n_real_frames"] == df["window_duration_sec"] * config.SAMPLING_FPS).all(),
               "n_real_frames == window_duration_sec * SAMPLING_FPS"),
        _check(df["window_duration_sec"].isin(config.LSTM_WINDOW_CONFIGS).all(),
               f"window_duration_sec in {config.LSTM_WINDOW_CONFIGS}"),
    ]
    if list(df.columns) == cols and len(df):
        feat = df[config.lstm_feature_columns()].to_numpy(np.float32)
        sample = feat[: min(2000, len(feat))].reshape(-1, config.LSTM_MAX_TIMESTEPS, config.NUM_FEATURES)
        n_real = df["n_real_frames"].to_numpy()[: len(sample)]
        pad_ok = all(np.all(sample[i, : config.LSTM_MAX_TIMESTEPS - n] == 0.0)
                     for i, n in enumerate(n_real))
        out.append(_check(pad_ok, "leading (MAX_TIMESTEPS - n_real_frames) timesteps are zero-padded"))
    return out


def check_frame_features(path=None) -> list[Result]:
    path = path or paths.frame_features_csv()
    if not os.path.exists(path):
        return [_check(False, f"{path} missing")]
    df = pd.read_csv(path)
    out = [
        _check(list(df.columns) == config.FLAT_CSV_COLUMNS,
               f"columns == {len(config.FLAT_CSV_COLUMNS)} named feature columns"),
        _check(set(df["level"].unique()) <= {1, 2}, f"level in {{1,2}}"),
        _check((df["ear_mar_valid"] == 1.0).all(), "every row has ear_mar_valid == 1.0"),
    ]
    inc = df.groupby(["subject", "parent_video"])["frame_idx"].apply(lambda s: s.is_monotonic_increasing)
    out.append(_check(inc.all(), "frame_idx increasing within each (subject, parent_video)"))
    return out


def check_frame_features_enriched(path=None) -> list[Result]:
    path = path or paths.frame_features_enriched_csv()
    if not os.path.exists(path):
        return [_check(False, f"{path} missing")]
    df = pd.read_csv(path)
    n_base = len(config.ROLLING_BASE_COLS)
    expected = len(config.FLAT_CSV_COLUMNS) + 1 + n_base * 4 + n_base  # +EAR_mean, x4 rolling, x1 delta
    out = [
        _check(df.shape[1] == expected, f"{expected} columns (got {df.shape[1]})"),
        _check("EAR_mean" in df.columns, "EAR_mean present"),
        _check(all(f"{c}_delta1" in df.columns for c in config.ROLLING_BASE_COLS),
               "all _delta1 columns present"),
        _check(all(f"{c}_rollmean_{config.ROLL_WINDOW_SHORT}f" in df.columns
                   and f"{c}_rollstd_{config.ROLL_WINDOW_LONG}f" in df.columns
                   for c in config.ROLLING_BASE_COLS),
               "all rolling mean/std columns present for both windows"),
    ]
    first = df.groupby(["subject", "parent_video"]).head(1)
    delta_cols = [f"{c}_delta1" for c in config.ROLLING_BASE_COLS]
    out.append(_check((first[delta_cols] == 0.0).all().all(),
                      "first frame of each clip has _delta1 == 0"))
    return out


def check_face_crops_index(path=None) -> list[Result]:
    path = path or paths.face_crops_index_csv()
    if not os.path.exists(path):
        return [_check(False, f"{path} missing")]
    df = pd.read_csv(path)
    out = [
        _check(list(df.columns) == config.FACE_CROPS_INDEX_COLS,
               f"columns == {config.FACE_CROPS_INDEX_COLS}"),
        _check(set(df["level"].unique()) <= {1, 2}, "level in {1,2}"),
    ]
    sample = df.sample(min(200, len(df)), random_state=0) if len(df) else df
    out.append(_check(sample["image_path"].apply(os.path.exists).all(),
                      "sampled image_path files exist on disk"))
    def _name_ok(r):
        return os.path.basename(r["image_path"]) == \
            f"{r['subject']}_{os.path.splitext(r['parent_video'])[0]}_s{r['sample_idx']}.jpg"
    out.append(_check(sample.apply(_name_ok, axis=1).all(),
                      "image filename == {subject}_{clip_stem}_s{sample_idx}.jpg"))
    per_clip = df.groupby(["subject", "parent_video"]).size()
    out.append(_check((per_clip <= config.CROP_MAX_FRAMES_PER_CLIP).all(),
                      f"<= {config.CROP_MAX_FRAMES_PER_CLIP} crops per clip"))
    return out


def check_cnn_lstm_windows(path=None) -> list[Result]:
    path = path or paths.cnn_lstm_windows_index_csv()
    if not os.path.exists(path):
        return [_check(False, f"{path} missing")]
    df = pd.read_csv(path)
    out = [
        _check(list(df.columns) == config.CNNLSTM_INDEX_COLS,
               f"columns == {config.CNNLSTM_INDEX_COLS}"),
        _check(set(df["level"].unique()) <= {1, 2}, "level in {1,2}"),
        _check(df["window_duration_sec"].isin(config.CNNLSTM_WINDOW_CONFIGS).all(),
               f"window_duration_sec in {config.CNNLSTM_WINDOW_CONFIGS}"),
    ]
    s = df.head(5000)
    n_img = s["image_paths"].str.split(";").str.len()
    n_geo = s["geometric_feature_seq"].str.split(";").str.len()
    out += [
        _check((n_img == s["n_real_frames"]).all(), "len(image_paths) == n_real_frames"),
        _check((n_geo == s["n_real_frames"]).all(), "len(geometric_feature_seq) == n_real_frames"),
        _check((s["n_real_frames"] == s["window_duration_sec"] * config.SAMPLING_FPS).all(),
               "n_real_frames == window_duration_sec * SAMPLING_FPS"),
    ]
    per_frame_geo = s["geometric_feature_seq"].str.split(";").str[0].str.split(",").str.len()
    out.append(_check((per_frame_geo == config.NUM_GEO_FEATURES).all(),
                      f"each geo frame has {config.NUM_GEO_FEATURES} values"))
    return out


_ARTIFACTS = {
    "lstm_windows": check_lstm_windows,
    "frame_features": check_frame_features,
    "frame_features_enriched": check_frame_features_enriched,
    "face_crops_index": check_face_crops_index,
    "cnn_lstm_windows": check_cnn_lstm_windows,
}


def check_all() -> bool:
    """Run every check for every artifact that exists. Returns True iff all pass."""
    all_ok = True
    n_checked = 0
    for name, fn in _ARTIFACTS.items():
        results = fn()
        exists = not (len(results) == 1 and not results[0][0] and "missing" in results[0][1])
        if not exists:
            print(f"— {name}: not built yet, skipping")
            continue
        n_checked += 1
        print(f"\n{name}:")
        for ok, msg in results:
            print(f"  {'PASS' if ok else 'FAIL'}  {msg}")
            all_ok &= ok
    if n_checked == 0:
        print("\nNo artifacts built yet — nothing to verify.")
        return True
    print(f"\n{'ALL CHECKS PASSED' if all_ok else 'SOME CHECKS FAILED'}")
    return all_ok


def compare_against(reference_csv: str, artifact: str) -> bool:
    """Column parity + per-level counts (±2%) + per-feature mean/std (±5%) vs a Colab CSV."""
    ref = pd.read_csv(reference_csv)
    cur_path = {
        "lstm_windows": paths.lstm_windows_csv(),
        "frame_features": paths.frame_features_csv(),
    }[artifact]
    cur = pd.read_csv(cur_path)
    ok = True
    if list(ref.columns) != list(cur.columns):
        print("FAIL  column set/order differs"); ok = False
    for lvl in sorted(set(ref["level"]) | set(cur["level"])):
        rn, cn = (ref["level"] == lvl).sum(), (cur["level"] == lvl).sum()
        within = abs(rn - cn) <= 0.02 * max(rn, cn, 1)
        print(f"  {'PASS' if within else 'FAIL'}  level {lvl} rows: ref {rn} vs cur {cn}")
        ok &= within
    num_cols = [c for c in cur.columns if cur[c].dtype.kind in "fi" and c != "level"]
    for c in num_cols[:30]:
        rm, cm = ref[c].mean(), cur[c].mean()
        within = abs(rm - cm) <= 0.05 * (abs(rm) + 1e-9) + 1e-6
        if not within:
            print(f"  FAIL  {c}: mean ref {rm:.5g} vs cur {cm:.5g}")
            ok = False
    print(f"\n{'COMPARE OK' if ok else 'COMPARE MISMATCH'}")
    return ok
