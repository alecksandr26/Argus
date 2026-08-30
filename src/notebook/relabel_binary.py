"""Argus — Relabel the existing dataset CSVs for binary classification.

Standalone Colab script (not a pipeline stage/notebook of its own) — paste this into a fresh
Colab cell and run it once. It does NOT re-extract anything: it reads whatever dataset CSVs
`01`/`02`/`06`/`09` have already produced on Drive, adds a `level_binary` (0/1) column and a
`class_name_binary` column to each, and writes them back in place. The original 3-class `level`
column is left untouched — this is additive, not destructive, so nothing about the existing
3-class notebooks breaks by running this.

Why this exists: every 3-class model tried in this project (RandomForest, Dense NN, the
single-frame CNN, both CNN+LSTM backbones) has been stuck in a 33-41% band, and `Low Vigilant`
(the middle class) has been the single most consistently broken class across every architecture
-- 0.0000 recall in two separate MobileNetV2 runs, not just low accuracy. See
`notebook/CLAUDE.md`'s "Cheaper options considered alongside the full CNN+LSTM build" (option 4)
for the full writeup. Collapsing to binary removes that specific failure mode, not just raises
the random-guess floor from 33.3% to 50%.

--- What to change in each training notebook after running this, to actually use the new column ---
This script only prepares the data. Each notebook still needs a small, mechanical edit:
  1. Wherever it does `df[...].to_numpy(dtype=np.int32) - 1` (or reads `df['level']` directly)
     to build labels, read `level_binary` instead of `level`.
  2. `CLASS_NAMES = ['Alert', 'Low Vigilant', 'Drowsy']` -> `CLASS_NAMES = BINARY_CLASS_NAMES`
     (below) -- 2 entries, not 3.
  3. Anywhere `num_classes` feeds a `Dense(num_classes, softmax)` head, it'll now resolve to 2
     automatically via `len(CLASS_NAMES)` -- no other model-definition changes needed.
  4. `StratifiedGroupKFold`/`compute_class_weight` calls that stratify on `df['level']` should
     stratify on `df['level_binary']` instead, so the class-balance stratification matches
     what's actually being predicted.
Not done here on purpose -- touches 01/02/04/05/06/07/09/10, a bigger, separate change than this
relabeling step, and worth doing deliberately per notebook rather than blindly find-replaced.
"""

import os
import pandas as pd

try:
    from google.colab import drive
    drive.mount('/content/drive')
except ImportError:
    pass  # Not running in Colab -- assume Drive is already mounted/available at the path below.

# --- Argus project paths (must match every other notebook in this pipeline) ---
project_folder = "/content/drive/MyDrive/Argus"
dataset_folder = f"{project_folder}/dataset"
processed_folder = f"{dataset_folder}/dataset_processed"

# --- Binary framing: pick ONE. See the module docstring above for the tradeoff. ---
# 'alert_vs_attention' (default, recommended): merges Low Vigilant + Drowsy into "Needs
#   Attention". Preserves Argus's preventive/early-warning positioning -- anything short of
#   fully alert trips the system, matching the root CLAUDE.md's "preventive layer" framing.
# 'drowsy_vs_not': merges Alert + Low Vigilant into "Not Drowsy". A purer "is this the
#   dangerous state right now" signal, but gives up early warning -- Low Vigilant no longer
#   gets flagged on its own.
BINARY_FRAMING = 'alert_vs_attention'

_FRAMINGS = {
    # level: 1=Alert, 2=Low Vigilant, 3=Drowsy (the convention used everywhere in this project).
    'alert_vs_attention': {
        'map': {1: 0, 2: 1, 3: 1},
        'class_names': ['Alert', 'Needs Attention'],
    },
    'drowsy_vs_not': {
        'map': {1: 0, 2: 0, 3: 1},
        'class_names': ['Not Drowsy', 'Drowsy'],
    },
}

if BINARY_FRAMING not in _FRAMINGS:
    raise ValueError(f"BINARY_FRAMING must be one of {list(_FRAMINGS)}, got {BINARY_FRAMING!r}")

LEVEL_TO_BINARY = _FRAMINGS[BINARY_FRAMING]['map']
BINARY_CLASS_NAMES = _FRAMINGS[BINARY_FRAMING]['class_names']

# Every dataset CSV in the pipeline that carries a 3-class `level` column. Missing files are
# skipped (not every stage has necessarily been run/rerun yet), not an error -- see the printed
# summary at the end for exactly what got relabeled vs. skipped.
TARGET_CSVS = [
    "lstm_windows.csv",              # 01 -> 03/08
    "frame_features.csv",            # 02 -> 04/05
    "frame_features_enriched.csv",   # 02 -> 04/05
    "face_crops_index.csv",          # 06 -> 07/09
    "cnn_lstm_windows_index.csv",    # 09 -> 10
]


def relabel_csv(csv_path, level_to_binary, class_names):
    df = pd.read_csv(csv_path)
    if 'level' not in df.columns:
        raise ValueError(f"'{csv_path}' has no 'level' column -- unexpected schema, not touched.")

    unmapped = set(df['level'].unique()) - set(level_to_binary)
    if unmapped:
        raise ValueError(
            f"'{csv_path}' has 'level' values {sorted(unmapped)} not covered by "
            f"LEVEL_TO_BINARY={level_to_binary}. Fix the mapping before relabeling -- silently "
            "dropping/mis-binning rows here would corrupt the dataset."
        )

    df['level_binary'] = df['level'].map(level_to_binary).astype(int)
    df['class_name_binary'] = df['level_binary'].map(dict(enumerate(class_names)))
    df.to_csv(csv_path, index=False)

    before = df['level'].value_counts().sort_index()
    after = df['level_binary'].value_counts().sort_index()
    return before, after, len(df)


def main():
    print(f"Binary framing: {BINARY_FRAMING} -> classes {BINARY_CLASS_NAMES}, "
          f"mapping {LEVEL_TO_BINARY}\n")

    for filename in TARGET_CSVS:
        csv_path = os.path.join(processed_folder, filename)
        if not os.path.exists(csv_path):
            print(f"⏭️  {filename}: not found, skipping (that stage hasn't been run yet).")
            continue

        before, after, n_rows = relabel_csv(csv_path, LEVEL_TO_BINARY, BINARY_CLASS_NAMES)
        print(f"✅ {filename}: {n_rows} rows relabeled in place.")
        print(f"   3-class counts:  {dict(before)}")
        print(f"   binary counts:   {dict(after)} ({BINARY_CLASS_NAMES[0]}={after.get(0, 0)}, "
              f"{BINARY_CLASS_NAMES[1]}={after.get(1, 0)})")
        print()

    print("Done. 'level' is untouched in every file above -- 'level_binary'/'class_name_binary' "
          "were added alongside it. See this script's module docstring for the small per-notebook "
          "edit still needed to actually train against the new column.")


if __name__ == "__main__":
    main()
