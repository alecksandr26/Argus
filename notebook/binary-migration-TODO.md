# Binary migration — training-notebook checklist

Status as of this file's creation: the **dataset-creation side is done** (`01` / `02` / `06` /
`09` read `dataset/raw_videos_binary/`, `CLASS_NAMES = ["Not Drowsy", "Drowsy"]`, `NUM_CLASSES =
2`, `map_level` validates `{1,2}`; `relabel_binary_raw_videos.ipynb` and
`extract_uta_rldd_clips.py` updated). See `CLAUDE.md`'s "Binary migration (in progress)".

The **training notebooks below are not migrated.** They will break the moment the binary CSVs
are regenerated. This is the deliberate-per-notebook checklist to work from — the model
definitions are already class-count-parametric (`num_classes = len(...)`, `Dense(num_classes,
softmax)`, `compute_class_weight('balanced', ...)`, dynamic focal loss), so the breakage is
concentrated in three idioms:

- `CLASS_NAMES = ['Alert', 'Low Vigilant', 'Drowsy']` literals → `['Not Drowsy', 'Drowsy']`
- `subject_level_sets == {1, 2, 3}` fold guards → `== {1, 2}`
- `report['Alert'] / report['Low Vigilant']` dict-key lookups in comparison/CV tables

`sparse_categorical_crossentropy` + `Dense(2, softmax)` stays valid — no need to switch to
`binary_crossentropy`/`Dense(1)`. `df['level'].to_numpy(...) - 1` → `{0,1}` is already correct;
only the adjacent `# 0=Alert, 1=Low Vigilant, 2=Drowsy` comments go stale. No
`to_categorical` / `get_dummies` / `np.eye(3)` / `_3class` filename tags exist anywhere.

Order the datasets get regenerated: `relabel_binary_raw_videos.ipynb` → `01`/`02` →
`03`/`04`/`05`; and `relabel_binary_raw_videos.ipynb` → `06` (**`reset_dataset=True`**) → `07`,
and → `09` → `10`.

---

## 03_model_training_lstm.ipynb — 1 blocker, no design questions

- [ ] **BLOCKER** cell ~18 (LSTM evaluation): `CLASS_NAMES = ['Alert', 'Low Vigilant', 'Drowsy']`
  → `['Not Drowsy', 'Drowsy']`. Feeds `classification_report(target_names=CLASS_NAMES)` and the
  confusion-matrix tick labels — a length-3 `target_names` against 2 classes raises `ValueError`.
- [ ] cell ~8 comment `# 'level' is already the 3-class label (1=Alert, 2=Low Vigilant, 3=Drowsy)`
  — stale (the `- 1` line itself is fine).
- [ ] cosmetic markdown: cells ~0/6/11 ("3 drowsiness levels", "3-class"), cell ~15 ("level 6,
  entering microsleep"; "RandomForest baseline above" — RF moved out long ago).
- SAFE, no change: `num_classes = len(np.unique(y_lstm))`, `Dense(num_classes)`,
  `compute_class_weight('balanced', ...)`, `GroupShuffleSplit` (no all-classes guard).
- Pre-existing (not binary): cell ~12 markdown says "128 and 64 units", code is `LSTM(64)`→`LSTM(32)`.

## 04_random_forest_training.ipynb — 1 blocker (1 def, 6 use sites)

- [ ] **BLOCKER** cell ~12: `CLASS_NAMES = ['Alert', 'Low Vigilant', 'Drowsy']` → 2 entries.
  Downstream `classification_report`/heatmap use sites to re-check after: cells ~12, ~19 (tuned
  RF), ~21 (per-subject-norm), ~28 (enriched), ~30 (HistGradientBoosting), ~33 (SMOTE).
- [ ] optional: cell ~18 `scoring='f1_macro'` and cell ~33 `f1_score(average='macro')` still run
  for binary (average over 2 classes) — switch to `'f1'` / `average='binary'` if you want the
  cleaner binary metric. Low priority.
- [ ] cosmetic / **reporting-sensitive**: cell ~14 "Diagnosing the Low Accuracy" hardcodes
  "32.6% overall accuracy (3-class, ... 14307/9901/13416)", "Drowsy recall ... 0.13", "barely
  above chance" (chance is now 50%, not 33.3%). Also cells ~13/17/22/23/34. Re-label or re-run.
- SAFE, no change: `y = df['level']` raw `{1,2}` (sklearn handles arbitrary labels, no `-1`),
  `class_weight='balanced'`, `GroupShuffleSplit`/`GroupKFold`.
- Pre-existing (not binary): cell ~18 hyperparameter search hangs — double `n_jobs=-1` nesting
  (already in root `CLAUDE.md`).

## 05_dense_nn_training.ipynb — 2 blockers, no design questions

- [ ] **BLOCKER** cell ~14: `CLASS_NAMES = ['Alert', 'Low Vigilant', 'Drowsy']` → 2 entries.
  Use sites: cells ~14, ~21 (regularized), ~23 (per-subject-norm), ~31 (enriched), ~34 (SMOTE).
- [ ] **BLOCKER** cell ~18: inline fallback
  `xticklabels=CLASS_NAMES if 'CLASS_NAMES' in globals() else ['Alert', 'Low Vigilant', 'Drowsy']`
  — the 3-element fallback must also become 2 (usually dead in a top-to-bottom run, still wrong).
- [ ] cell ~6 / ~27 comment `# 0-indexed: 0=Alert, 1=Low Vigilant, 2=Drowsy` — stale.
- [ ] cosmetic / **reporting-sensitive**: cell ~16 "Diagnosing the Overfitting" ("97%+ training
  accuracy", "~41%", "38.6% test accuracy", "same ~33-40% band"). Also cells ~0/9/15/24/35.
- SAFE, no change: `num_classes = len(np.unique(y))`, all 4 variants `Dense(num_classes)` +
  `sparse_categorical_crossentropy`, `compute_class_weight('balanced', ...)`.

## 07_cnn_training.ipynb — 4 blockers, 1 minor design question

- [ ] **BLOCKER** cell ~4 `split_by_subject_fold`:
  `complete_subjects = subject_level_sets[subject_level_sets == {1, 2, 3}].index` → `== {1, 2}`.
  With binary data no subject matches `{1,2,3}` → `complete_subjects` empty →
  `ValueError("Only 0 subject(s) have all 3 classes")`. Also fix the two `ValueError` message
  strings ("have all 3 classes" → "both classes").
- [ ] **BLOCKER** cell ~6: `CLASS_NAMES = ['Alert', 'Low Vigilant', 'Drowsy']` → 2. Drives
  `num_classes = len(CLASS_NAMES)` (cell ~8) and every report/heatmap.
- [ ] **BLOCKER** cell ~19 (MobileNetV2 comparison table): rows built from
  `report['Alert']['recall']`, `report['Low Vigilant']['recall']`, `report['Drowsy']['recall']`
  → `KeyError` against a binary report dict. Rewrite to the 2 binary keys.
- [ ] **BLOCKER** cell ~21 (`RUN_CROSS_VALIDATION`, default `True`):
  `fold_report['Alert']['recall']`, `fold_report['Low Vigilant']['recall']` — same `KeyError`.
- [ ] **DESIGN (minor)**: cell ~11 `DROWSY_WEIGHT_BOOST = 2.0` on top of balanced weights — still
  wanted for binary? Drowsy is still the safety-critical minority, so probably yes; low stakes.
- [ ] cell ~11 comment `DROWSY_LABEL = CLASS_NAMES.index('Drowsy')  # 2` (now 1); long comment
  block about "Alert (0.28 recall) and Low Vigilant (0.25 recall)".
- [ ] cosmetic: cell ~3 ("Not every subject currently has all 3 classes" ×2), cell ~2 ("trains
  two 3-class variants"), cell ~4 `missing_in_val` warning text "labels=[0,1,2]", cells ~20/22/23.
- SAFE, no change: `SparseCategoricalFocalLoss` reads `tf.shape(y_pred)[1]` dynamically;
  `build_cnn_scratch` / `build_mobilenet_backbone` both `Dense(num_classes, softmax)`;
  `MacroF1Callback` `labels=list(range(num_classes))`; `DROWSY_LABEL = CLASS_NAMES.index('Drowsy')`
  still resolves (to 1) as long as the exact string `'Drowsy'` stays in `CLASS_NAMES`.

## 08_deployment_export_lstm.ipynb — export path needs nothing; sim cell only

- [ ] **The deployment artifact (`LstmGeometricFeatureModel`, cell ~12) needs ZERO changes** — no
  class-count parameter; the output softmax width is inherited from the wrapped LSTM (notebook
  03). `num_features = 58` is a feature-vector dimension, **not** class count — do not touch it.
- [ ] **BLOCKER** cell ~19 (end-to-end simulation):
  `CLASS_NAMES = {1: "Alert", 2: "Low Vigilant", 3: "Drowsy"}` → `{1: "Not Drowsy", 2: "Drowsy"}`.
- [ ] cell ~19: `ORIGINAL_LEVEL_TO_CLASS = {1:1, 2:1, 3:2, 4:2, 5:3, 6:3}` + the
  `EXTERNAL_SUBJECT_START = 7` / `subject_num >= EXTERNAL_SUBJECT_START` branch — stale 6-level→
  3-class + subject-number machinery (already dead pre-binary; doubly wrong now). Rewrite to just
  parse `level_1` / `level_2` from the filename via `map_level`.
- [ ] **DESIGN (minor)**: cell ~3 `raw_videos_folder` — point the simulation at
  `raw_videos_binary/`, matching the dataset notebooks.
- [ ] cosmetic: cell ~0 "a 3-class softmax drowsiness class out". (cell ~13 "(batch, 52)" is a
  pre-existing off-by-one, should be 51 — not binary-related.)

## 10_cnn_lstm_training.ipynb — 3 blockers + 1 real design decision

- [ ] **BLOCKER** cell ~4 `split_by_subject_fold`: `subject_level_sets == {1, 2, 3}` → `== {1, 2}`
  (+ `ValueError` message text). Same failure mode as `07`.
- [ ] **BLOCKER** cell ~6: `CLASS_NAMES = ['Alert', 'Low Vigilant', 'Drowsy']` → 2.
- [ ] **BLOCKER** cell ~14: `LOW_VIGILANT_LABEL = CLASS_NAMES.index('Low Vigilant')` runs
  **unconditionally** (it is *not* inside the `if USE_CLASS_WEIGHTS:` block) →
  `ValueError: 'Low Vigilant' is not in list` the moment `CLASS_NAMES` is binary, even with
  `USE_CLASS_WEIGHTS = False`. Delete it or move it inside the guard.
- [ ] **DESIGN DECISION (real)**: cell ~14 asymmetric class-weight scheme — `DROWSY_WEIGHT_BOOST
  = 1.5` **and** `LOW_VIGILANT_WEIGHT_DAMPEN = 0.5` (`weight_dict[LOW_VIGILANT_LABEL] *= ...`).
  Low Vigilant is no longer a class, so the "dampen the ambiguous middle class" half is
  meaningless. Currently inert (`USE_CLASS_WEIGHTS = False`). Recommended (fits the standing
  "one committed implementation, no toggles" preference): rip out the asymmetric constants and
  all `LOW_VIGILANT_*`; if weighting is ever re-enabled, use plain `'balanced'`, optionally with
  a Drowsy boost. Alternative: keep the toggle.
- [ ] cosmetic / **reporting-sensitive**: cells ~0/3/17/27/33 — "all 3 classes", "random-guess
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
