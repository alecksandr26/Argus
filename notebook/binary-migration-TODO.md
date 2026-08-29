# Binary migration — training-notebook checklist

## ✅ CODE MIGRATION COMPLETE (all ten notebooks) — no rerun has happened yet

Every notebook `01`–`10` is migrated in code. Dataset-creation (`01`/`02`/`06`/`09`) reads
`dataset/raw_videos_binary/`; training + export (`03`/`04`/`05`/`07`/`08`/`10`) had their
`CLASS_NAMES` literals, `== {1,2,3}` fold guards, and `report['Alert']`/`report['Low Vigilant']`
dict-key lookups converted. Model definitions were already class-count-parametric —
`sparse_categorical_crossentropy` + `Dense(num_classes, softmax)` was kept, not switched to
`Dense(1)`/`binary_crossentropy`; macro-F1 was kept everywhere for cross-model comparability. The
per-notebook sections below are the record of what changed in each; the checkboxes are done.

Two non-mechanical changes: `07` + `10`'s `split_by_subject_fold` now draws its **validation**
fold from a second `StratifiedGroupKFold` fold instead of the (now-empty under binary) pool of
class-incomplete subjects; `10`'s `LOW_VIGILANT_*` asymmetric class-weight constants were deleted
(the `DROWSY_WEIGHT_BOOST` and `USE_CLASS_WEIGHTS` toggle are kept).

**Still pending: the reruns.** Order: `relabel_binary_raw_videos.ipynb` (done) → `01`/`02` →
`03`/`04`/`05`; and → `06` (**`reset_dataset=True`**) → `07`, and → `09` → `10`; then `08` after
`03` retrains. Until then every notebook loads its old 3-class Drive artifacts and all attached
outputs are the pre-migration record.

`src/cv-argus`'s `_CLASS_NAMES` / `_STATUS_COLORS` (bottom of this file) stay 3-class until a
binary `.keras` model actually exists to deploy.

---

## 03_model_training_lstm.ipynb — DONE

- [x] **BLOCKER** cell ~18 (LSTM evaluation): `CLASS_NAMES = ['Alert', 'Low Vigilant', 'Drowsy']`
  → `['Not Drowsy', 'Drowsy']`. Feeds `classification_report(target_names=CLASS_NAMES)` and the
  confusion-matrix tick labels — a length-3 `target_names` against 2 classes raises `ValueError`.
- [x] cell ~8 comment `# 'level' is already the 3-class label (1=Alert, 2=Low Vigilant, 3=Drowsy)`
  — stale (the `- 1` line itself is fine).
- [x] cosmetic markdown: cells ~0/6/11 ("3 drowsiness levels", "3-class"), cell ~15 ("level 6,
  entering microsleep"; "RandomForest baseline above" — RF moved out long ago).
- SAFE, no change: `num_classes = len(np.unique(y_lstm))`, `Dense(num_classes)`,
  `compute_class_weight('balanced', ...)`, `GroupShuffleSplit` (no all-classes guard).
- Pre-existing (not binary): cell ~12 markdown says "128 and 64 units", code is `LSTM(64)`→`LSTM(32)`.

## 04_random_forest_training.ipynb — DONE

- [x] **BLOCKER** cell ~12: `CLASS_NAMES = ['Alert', 'Low Vigilant', 'Drowsy']` → 2 entries.
  Downstream `classification_report`/heatmap use sites to re-check after: cells ~12, ~19 (tuned
  RF), ~21 (per-subject-norm), ~28 (enriched), ~30 (HistGradientBoosting), ~33 (SMOTE).
- [x] optional: cell ~18 `scoring='f1_macro'` and cell ~33 `f1_score(average='macro')` still run
  for binary (average over 2 classes) — switch to `'f1'` / `average='binary'` if you want the
  cleaner binary metric. Low priority.
- [x] cosmetic / **reporting-sensitive**: cell ~14 "Diagnosing the Low Accuracy" hardcodes
  "32.6% overall accuracy (3-class, ... 14307/9901/13416)", "Drowsy recall ... 0.13", "barely
  above chance" (chance is now 50%, not 33.3%). Also cells ~13/17/22/23/34. Re-label or re-run.
- SAFE, no change: `y = df['level']` raw `{1,2}` (sklearn handles arbitrary labels, no `-1`),
  `class_weight='balanced'`, `GroupShuffleSplit`/`GroupKFold`.
- Pre-existing (not binary): cell ~18 hyperparameter search hangs — double `n_jobs=-1` nesting
  (already in root `CLAUDE.md`).

## 05_dense_nn_training.ipynb — DONE

- [x] **BLOCKER** cell ~14: `CLASS_NAMES = ['Alert', 'Low Vigilant', 'Drowsy']` → 2 entries.
  Use sites: cells ~14, ~21 (regularized), ~23 (per-subject-norm), ~31 (enriched), ~34 (SMOTE).
- [x] **BLOCKER** cell ~18: inline fallback
  `xticklabels=CLASS_NAMES if 'CLASS_NAMES' in globals() else ['Alert', 'Low Vigilant', 'Drowsy']`
  — the 3-element fallback must also become 2 (usually dead in a top-to-bottom run, still wrong).
- [x] cell ~6 / ~27 comment `# 0-indexed: 0=Alert, 1=Low Vigilant, 2=Drowsy` — stale.
- [x] cosmetic / **reporting-sensitive**: cell ~16 "Diagnosing the Overfitting" ("97%+ training
  accuracy", "~41%", "38.6% test accuracy", "same ~33-40% band"). Also cells ~0/9/15/24/35.
- SAFE, no change: `num_classes = len(np.unique(y))`, all 4 variants `Dense(num_classes)` +
  `sparse_categorical_crossentropy`, `compute_class_weight('balanced', ...)`.

## 07_cnn_training.ipynb — DONE (migrated, not yet re-run)

All four blockers fixed plus one design change the checklist hadn't anticipated:

- [x] cell 4 `split_by_subject_fold`: completeness guard `== {1, 2, 3}` → `== {1, 2}`; `ValueError`
  strings updated ("both classes"); minimum-subject threshold 2 → 3 (need one test + one val fold).
- [x] **DESIGN CHANGE (not in the original checklist):** the split derived its **validation** set
  from the pool of class-incomplete subjects. Under binary labels essentially every UTA-RLDD
  subject has both classes (an alert clip → level 1, a drowsy clip → level 2), so that pool is
  empty and `df_val` would be empty → `ValueError`. Rewrote it: `val` is now a second
  `StratifiedGroupKFold` fold, `(fold_idx + 1) % n_splits`, disjoint from the test fold; any
  genuinely single-class subject folds into training. The `missing_in_val` warning block at the
  end of cell 4 is gone (val now always has both classes, guaranteed by the function).
- [x] cell 6: `CLASS_NAMES` → `['Not Drowsy', 'Drowsy']`; label comment updated.
- [x] cell 19: MobileNetV2 comparison table → two binary report keys (`Not Drowsy` / `Drowsy`).
- [x] cell 21: CV-diagnostic per-class recall → two binary keys.
- [x] cell 11: `DROWSY_WEIGHT_BOOST = 2.0` **kept** (Drowsy is still the safety-critical minority);
  `DROWSY_LABEL` comment `# 2` → `# 1`; the long 3-class recall commentary rewritten;
  `MacroF1Callback` docstring and the "incomplete-subject pool" callback comment updated.
- [x] cosmetic: cell 2 ("two binary variants"), cell 3 (completeness paragraph), cells 20 & 22
  (dropped stale 3-class numbers; cell 22 also updated for `06`'s 1→5 FPS change).
- Stale outputs cleared on every code cell that changed (2/4/6/11/19/21).
- SAFE, unchanged as predicted: focal loss / `Dense(num_classes)` / `num_classes = len(CLASS_NAMES)`
  are all class-count-parametric. MobileNetV2 stays enabled in `07` (only `10` disables it).

**Still needs:** `06` rebuilt (binary, `reset_dataset=True`) before `07` can run; no binary run yet.

## 08_deployment_export_lstm.ipynb — DONE (sim cell + dead 6-level machinery removed)

- [x] **The deployment artifact (`LstmGeometricFeatureModel`, cell ~12) needs ZERO changes** — no
  class-count parameter; the output softmax width is inherited from the wrapped LSTM (notebook
  03). `num_features = 58` is a feature-vector dimension, **not** class count — do not touch it.
- [x] **BLOCKER** cell ~19 (end-to-end simulation):
  `CLASS_NAMES = {1: "Alert", 2: "Low Vigilant", 3: "Drowsy"}` → `{1: "Not Drowsy", 2: "Drowsy"}`.
- [x] cell ~19: `ORIGINAL_LEVEL_TO_CLASS = {1:1, 2:1, 3:2, 4:2, 5:3, 6:3}` + the
  `EXTERNAL_SUBJECT_START = 7` / `subject_num >= EXTERNAL_SUBJECT_START` branch — stale 6-level→
  3-class + subject-number machinery (already dead pre-binary; doubly wrong now). Rewrite to just
  parse `level_1` / `level_2` from the filename via `map_level`.
- [x] **DESIGN (minor)**: cell ~3 `raw_videos_folder` — point the simulation at
  `raw_videos_binary/`, matching the dataset notebooks.
- [x] cosmetic: cell ~0 "a 3-class softmax drowsiness class out". (cell ~13 "(batch, 52)" is a
  pre-existing off-by-one, should be 51 — not binary-related.)

## 10_cnn_lstm_training.ipynb — DONE (split_by_subject_fold ported from 07; LOW_VIGILANT_* removed)

- [x] **BLOCKER** cell ~4 `split_by_subject_fold`: `subject_level_sets == {1, 2, 3}` → `== {1, 2}`
  (+ `ValueError` message text). Same failure mode as `07`.
- [x] **BLOCKER** cell ~6: `CLASS_NAMES = ['Alert', 'Low Vigilant', 'Drowsy']` → 2.
- [x] **BLOCKER** cell ~14: `LOW_VIGILANT_LABEL = CLASS_NAMES.index('Low Vigilant')` runs
  **unconditionally** (it is *not* inside the `if USE_CLASS_WEIGHTS:` block) →
  `ValueError: 'Low Vigilant' is not in list` the moment `CLASS_NAMES` is binary, even with
  `USE_CLASS_WEIGHTS = False`. Delete it or move it inside the guard.
- [x] **DESIGN DECISION (real)**: cell ~14 asymmetric class-weight scheme — `DROWSY_WEIGHT_BOOST
  = 1.5` **and** `LOW_VIGILANT_WEIGHT_DAMPEN = 0.5` (`weight_dict[LOW_VIGILANT_LABEL] *= ...`).
  Low Vigilant is no longer a class, so the "dampen the ambiguous middle class" half is
  meaningless. Currently inert (`USE_CLASS_WEIGHTS = False`). Recommended (fits the standing
  "one committed implementation, no toggles" preference): rip out the asymmetric constants and
  all `LOW_VIGILANT_*`; if weighting is ever re-enabled, use plain `'balanced'`, optionally with
  a Drowsy boost. Alternative: keep the toggle.
- [x] cosmetic / **reporting-sensitive**: cells ~0/3/17/27/33 — "all 3 classes", "random-guess
  chance for this balanced 3-class test set" (now 50%), "35.04% / 0.3296 macro-F1", the
  "Low Vigilant" recall discussion.
- COMMENTED OUT (note only): cells ~28/30/32 (MobileNetV2 frozen/fine-tune/comparison) are
  commented out. If ever re-enabled, cell ~32's comparison table has the same `report['Alert']`
  / `report['Low Vigilant']` `KeyError` pattern as `07` cell ~19.
- SAFE, no change: cell ~10 `num_classes = len(CLASS_NAMES)`; `build_cnn_lstm` /
  `build_scratch_feature_extractor` / `build_mobilenet_feature_extractor` all `Dense(num_classes)`
  derived; cell ~16 `evaluate_variant` uses `target_names=CLASS_NAMES` + `average='macro'` with
  **no** hardcoded class-name dict keys, so it just works once `CLASS_NAMES` is fixed; cells
  ~18-26 (frozen-CNN-embedding variant) — `- 1` labels, `Dense(num_classes)` derived.

---

## Also, whenever a binary model is actually deployed

`src/cv-argus` is not migrated. When a binary `.keras` model is trained and deployed:

- [ ] `src/model/detector.py` — `_CLASS_NAMES = {1: "Alert", 2: "Low Vigilant", 3: "Drowsy"}` →
  `{1: "Not Drowsy", 2: "Drowsy"}` (the one silent-mislabel site — `DetectionResult.level =
  argmax + 1` already generalizes), plus the four comment/docstring sites in that file
  (`# 1 = Alert, 3 = Drowsy`, `shape (3,)`, `output layer sized to 3 classes`).
- [ ] `src/pipeline/mjpeg_output_stage.py` — `_STATUS_COLORS` keys `{"Alert", "Low Vigilant",
  "Drowsy"}` → `{"Not Drowsy", "Drowsy"}` (demo overlay only; degrades gracefully otherwise).
- [ ] doc cleanup: `src/constants.py:15`, `src/model/lstm_model.py:6` (says "6-class" — stale
  pre-binary too), `README.md:46,51`, `src/cv-argus/CLAUDE.md` (L12, L317, L469-473).

Nothing else in `src/cv-argus` has a class-count or class-name assumption — a binary model swap
is a new Drive file ID + the above, no pipeline-structure work.
