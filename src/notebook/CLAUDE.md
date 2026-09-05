# CLAUDE.md — notebook

This file provides guidance to Claude Code when working in `notebook/`, Argus's ML pipeline: from
raw drowsiness-labeled video clips, through four different model families, to a deployable
artifact. It's a set of Google Colab notebooks (Drive as the storage backend, no local dataset or
build system — see the root `CLAUDE.md` for the full technical contract), not a package with
imports; each notebook re-mounts Drive and redefines its own constants in a "Setup" section, and
notebooks only hand off to each other via Drive CSV/model artifacts, never shared Python state.

This file is the empirical/decision summary: what each notebook does, what's actually been run,
what the results were, and — the reason this file exists — why a CNN-based model is the most
likely next backbone. Read it before proposing a next step on the pipeline, so the proposal
builds on what was already tried rather than re-litigating it. For full technical depth (exact
feature layouts, padding conventions, sync requirements between files, etc.), see the root
[`CLAUDE.md`](../../CLAUDE.md)'s "Notebook architecture" and "Model results and current status"
sections; this file stays at the level of "what happened and why," not implementation detail —
don't duplicate implementation-level edits' rationale here, put it in the root file instead.

## Dataset creation now runs locally, not in Colab

The four dataset-creation notebooks — `01_dataset_creation_lstm`, `02_dataset_creation_flat`,
`06_dataset_creation_face_crops`, `09_dataset_creation_cnn_lstm` — have been reimplemented as
local CPU-parallel scripts under **`src/dataset/`** (Colab kept interrupting the multi-hour
runs). Those scripts are the source of truth for dataset creation now; these four notebooks are
kept as Colab-runnable reference but are no longer the thing to edit. If you change feature
extraction / windowing / sampling, change it in `src/dataset/argus_dataset/` (constants in
`config.py`) and mirror the constant back into the notebook. Training notebooks
(`03`/`04`/`05`/`07`/`08`/`10`) are unaffected and still run on Colab. See
`src/dataset/README.md`.

The local pipeline has **no relabel step** — it reads `src/dataset/raw/raw_videos/` directly
and expects clips already labelled `level_1` (Not Drowsy) / `level_2` (Drowsy). The
`relabel_binary_raw_videos.ipynb` notebook (which derived a `raw_videos_binary/` tree on Drive)
is only relevant to the Colab flow described in "Binary migration" below.

## Binary migration (code done, reruns pending)

**Decision: the class scheme is binary — `Not Drowsy` vs. `Drowsy`** (`drowsy_vs_not`): `level_1`
= `Not Drowsy` (old `Alert` + `Low Vigilant`, merged), `level_2` = `Drowsy` (old `Drowsy`). This
was option 4 from "Cheaper options considered alongside the full CNN+LSTM build" below, now
chosen and implemented across every notebook — not still under evaluation. Rationale: `Low
Vigilant`, the ambiguous middle class, was the single most consistently broken class across every
3-class model (0.0000 recall in two separate MobileNetV2 runs); collapsing it removes that
failure mode rather than just raising the random-guess floor to 50%. The alternative framing
(`Alert` vs. `Needs Attention`, merging `Low Vigilant` + `Drowsy`) was considered and not chosen
— `drowsy_vs_not` gives the purer "is this the dangerous state" signal.

**Mechanism.** `relabel_binary_raw_videos.ipynb` builds a parallel Drive tree
`dataset/raw_videos_binary/subject_NN/level_<1-2>_clip_<NN>.mp4` by collapsing the 3-class
`dataset/raw_videos/` tree (`{1,2}→1, 3→2`); `raw_videos/` is left untouched as the 3-class
source of truth (so a different binary framing stays a one-line change, and
`extract_uta_rldd_clips.py` keeps writing 3-class `level_<1-3>` clips). The dataset-creation
notebooks read `raw_videos_binary/`, not `raw_videos/`.

**Code migration: complete across all ten notebooks.** `01` / `02` / `06` / `09` (dataset
creation) are repointed to `raw_videos_binary/` — `CLASS_NAMES = ["Not Drowsy", "Drowsy"]`,
`NUM_CLASSES = 2`, `map_level` validates `{1,2}`. `03` / `04` / `05` / `07` / `08` / `10`
(training + export) had their `CLASS_NAMES` literals, `== {1,2,3}` fold guards, and
`report['Alert']`/`report['Low Vigilant']` dict-key lookups converted to the two binary classes;
model definitions were already class-count-parametric (`Dense(num_classes)` +
`sparse_categorical_crossentropy`, kept — not switched to `Dense(1)`/`binary_crossentropy`), and
macro-F1 was kept everywhere for cross-model comparability. Per-notebook detail is in
[`binary-migration-TODO.md`](./binary-migration-TODO.md).

Two changes went beyond the mechanical checklist:
- **`07` and `10`'s `split_by_subject_fold`**: the split used to draw its **validation** set from
  the pool of class-*incomplete* subjects, but under binary labels essentially every UTA-RLDD
  subject has both classes (an alert clip → level 1, a drowsy clip → level 2), so that pool is
  empty. Validation is now a separate `StratifiedGroupKFold` fold (`(fold_idx + 1) % n_splits`),
  disjoint from the test fold; genuinely single-class subjects fold into training. `07`'s version
  was written first and `10`'s is a port of it.
- **`10`'s class weights**: the old asymmetric `DROWSY_WEIGHT_BOOST` + `LOW_VIGILANT_WEIGHT_DAMPEN`
  scheme lost its meaning (`Low Vigilant` is gone). All `LOW_VIGILANT_*` were deleted; the
  `DROWSY_WEIGHT_BOOST` (Drowsy still the safety-critical minority) and the `USE_CLASS_WEIGHTS`
  diagnostic toggle are kept, matching `07`.

**Rerun status: the `06` → `09` → `10` (face-crop / CNN+LSTM) chain has now actually been rerun
against binary data — every other notebook still hasn't.** `06` was rebuilt against
`raw_videos_binary/` (5-FPS/100-frame-cap crops), `09` rerun against that rebuild (5731 windows,
54 subjects, `geometric_feature_seq` fusion populated), and `10` trained on the result for the
first time under `Not Drowsy`/`Drowsy` labels — see "What we found" for the real numbers. `01`,
`02`, `03`, `04`, `05`, `07`, and `08` still read their old 3-class Drive artifacts, so their
attached outputs and every accuracy number for those notebooks in this file remain the
pre-migration 3-class record — this includes `07`, whose stale 3-class checkpoint `10`'s
frozen-embedding variant is currently (and knowingly) reusing as a frozen feature extractor. The
remaining regeneration order is: `01`/`02` → `03`/`04`/`05`; and `07` (now unblocked, since `06`
is rebuilt); then `08` after `03` retrains. `src/cv-argus`'s `_CLASS_NAMES` / `_STATUS_COLORS`
are now binary (`Not Drowsy`/`Drowsy`), matching the binary fused model (`06`→`09`→`10`/`11`
chain) it deploys — see the root `CLAUDE.md`'s "Current deployment status".

`relabel_binary.py` (the earlier post-hoc-`level_binary`-CSV-column approach) is **superseded
and must not be run** — its default `BINARY_FRAMING` is `alert_vs_attention` (the *wrong*
framing) and it rewrites the dataset CSVs in place, so running it as-shipped silently
mis-collapses every dataset CSV. It's kept only as a record of the rejected approach.

`src/cv-argus/src/model/detector.py`'s `_CLASS_NAMES` is now `{1: "Not Drowsy", 2: "Drowsy"}`
and `mjpeg_output_stage.py`'s status-colour map matches — both updated once the fused model was
actually deployed (see the root `CLAUDE.md`'s "Current deployment status").

**Most of "What we found" and "Why CNN is the most probable next backbone" below is still the
pre-migration 3-class record** — the numbers are real and worth keeping, but don't read binary
expectations into them. **One exception: the "`10` has since been run for the first time under
the actual binary label scheme" subsection near the end of "What we found"** is a genuine binary
result (the `06`→`09`→`10` chain only — see "Rerun status" above) and should be read as current,
not historical.

## Pipeline map

| # | Notebook | Reads | Writes | Status |
|---|---|---|---|---|
| 01 | `dataset_creation_lstm` | raw videos | `lstm_windows.csv` | ⚠️ prior run was at `sampling_fps = 10` / `MAX_TIMESTEPS = 60`; both now 5 / 30 (plus a streaming CSV write) — needs a `reset_dataset=True` rerun to regenerate `lstm_windows.csv` |
| 02 | `dataset_creation_flat` | raw videos | `frame_features.csv`, `frame_features_enriched.csv` | ⚠️ prior run was at `sampling_fps = 10`; now 5 — needs a rerun to regenerate both CSVs |
| 03 | `model_training_lstm` | `lstm_windows.csv` | LSTM `.keras` + scaler | ⚠️ migrated to binary; `MAX_TIMESTEPS` now 30 (was 60) to match `01`; needs retraining once `01` is rerun |
| 04 | `random_forest_training` | `frame_features.csv`/enriched | RF `.joblib` + scaler | ⚠️ migrated to binary; baseline (3-class) run only, tuning/augmentation incomplete; needs a rerun after `02` |
| 05 | `dense_nn_training` | `frame_features.csv`/enriched | Dense NN `.keras` + scaler | ⚠️ migrated to binary; prior runs were 3-class; needs a rerun after `02` |
| 06 | `dataset_creation_face_crops` | raw videos | `face_crops_index.csv` + `.jpg`s | ✅ rebuilt against `raw_videos_binary/` at `sampling_fps=5`/`MAX_FRAMES_PER_CLIP=100` — inferred from `09`'s binary-labeled, correctly-sized window index below (no direct `06` log captured yet). Earlier 24-subject/20-complete-class figures elsewhere in this file predate this rebuild |
| 07 | `cnn_training` | `face_crops_index.csv` | CNN `.keras` | ✅ **now re-run against the rebuilt binary `06` crop set** (50 complete-class subjects, same `split_by_subject_fold` as `11`, 10/34/10 val/train/test subjects). First genuine binary single-frame number: **59.64% acc / 0.5273 macro-F1** (Not Drowsy P/R 0.68/0.73, Drowsy P/R 0.38/0.32) on 8700 test crops — real signal above the ~66%-majority-class floor's macro-F1 (≈0.40), but well below what the same crops produce once aggregated over time by `11`'s frozen-embedding LSTM (see that row and "What we found"). `11`'s frozen-embedding variant now loads *this* checkpoint (`best_cnn_scratch_face_crops.keras`), not a stale pre-binary one — the staleness caveat that applied to the earlier 0.6213 result no longer applies. Superseded 3-class numbers (36.67% acc / 0.3614 macro-F1 from-scratch; 16.81% MobileNetV2) are historical only — see "What we found" |
| 08 | `deployment_export_lstm` | LSTM `.keras` + scaler | deployed `LstmGeometricFeatureModel` | ⬜ not re-run; migrated to binary (sim cell only — the export artifact needed no class change); `sampling_fps`/`MAX_TIMESTEPS` updated to 5 / 30 to match `01`/`03` — needs a rerun once `03` retrains |
| 09 | `dataset_creation_cnn_lstm` | `face_crops_index.csv` | `cnn_lstm_windows_index.csv` | ✅ rerun with **minority-class overlap-tiling active** (`CNNLSTM_MINORITY_WINDOW_OVERLAP=0.5` on `level_2`/Drowsy clips) — inferred from `11_drive_pull`'s near-balanced train split (2270 Not Drowsy / 1900 Drowsy windows) and larger-than-before window counts (Train 4170 / Val 1441 / Test 1440, vs. the pre-tiling 4359/234/1170 split). See "What we found" |
| 10 | `cnn_lstm_training` | `cnn_lstm_windows_index.csv` | CNN+LSTM `.keras` (from-scratch + frozen-CNN-embedding variants; MobileNetV2 disabled) | ✅ first real binary run (pre-rebalancing, `USE_CLASS_WEIGHTS=False`): from-scratch 69.89% acc / 0.5762 macro-F1 (Drowsy recall 0.24); frozen-CNN-embedding 67.07% acc / 0.6213 macro-F1 (Drowsy recall 0.46) — since superseded as the project's best result by `11_drive_pull`'s rerun on the rebalanced windows + retrained `07` checkpoint (see row 11 and "What we found") |
| 11 | `cnn_lstm_training_{upload,drive_pull}` | `cnn_lstm_windows_index[_forcolab].csv` + `face_crops.zip` | same as `10` | ✅ **`drive_pull`'s LR bug is fixed and it has been rerun for real — this is now the project's best result, by a wide margin.** Frozen-CNN-embedding + LSTM: **84.24% acc / 0.8375 macro-F1** argmax (84.38%/0.8379 at chosen threshold `t*=0.57`; Not Drowsy P/R 0.80/0.94, Drowsy P/R 0.91/0.73 argmax) — nearly 2x `07`'s single-frame macro-F1 on the same held-out subjects, and far above the earlier `upload`-sourced 0.6213. From-scratch CNN+LSTM also improved (62.29% acc / 0.6078 macro-F1) but stays well behind the frozen-embedding variant. The from-scratch+frozen ensemble is *worse* than frozen alone (0.8255 macro-F1) — don't deploy the ensemble. See "What we found" for the full numbers and caveats (single fold, no cross-validation yet; overlap-tiled Drowsy test windows aren't fully independent samples) |

Four model families share two dataset-creation notebooks:

```
01 dataset_creation_lstm  ──► lstm_windows.csv   ──► 03 model_training_lstm ──► 08 deployment_export_lstm
02 dataset_creation_flat  ──► frame_features.csv ──► 04 random_forest_training
                                                  └─► 05 dense_nn_training
06 dataset_creation_face_crops ──► face_crops_index.csv ──► 07 cnn_training
                                                         └─► 09 dataset_creation_cnn_lstm ──► 10 cnn_lstm_training
```

## What we found

**Update since this was first written, twice over: `src/cv-argus` moved from the geometric-only
LSTM (never deployed) to the single-frame CNN (below) to, now, the fused CNN-embedding +
geometric-feature + LSTM classifier from `11_cnn_lstm_training_drive_pull.ipynb`** — see the
root `CLAUDE.md`'s "Current deployment status" for the full history and
`src/cv-argus/CLAUDE.md`'s "Current status" for the deployment-side detail. The single-frame
CNN's own findings below are historical record now, not the current deployment, but they're
still the reason a CNN-based approach was worth pursuing at all: **raw pixels carry information
the geometric features never captured**, and this project's central "temporal context beats a
single frame" hypothesis (recurrence over a buffered window captures duration/velocity of eye
closure — see `03_model_training_lstm.ipynb`'s Design Decision cell) is what the fused model's
much stronger result (0.8375 macro-F1 vs. the CNN's own 0.5273 on the same subjects) finally
confirmed directly, rather than just arguing for.

**RandomForest and Dense NN, trained on single per-frame features, hit a real ceiling around
33-41% accuracy** — and we now have direct statistical proof of *why*, not just a suspicion:

- RandomForest baseline (7 curated features): **32.6%** accuracy, `Drowsy` recall collapsed to 0.13.
- Dense NN (all 58 features, best regularized pass): **38.6-40.8%**.
- **Root cause, measured directly:** Spearman correlation between any single per-frame feature and
  drowsiness level tops out at |r| = 0.26. `EAR_mean` — the feature this whole geometric pipeline
  was designed around — sits at |r| = 0.04, essentially zero. A single frame's instantaneous
  eye-openness value just doesn't carry much signal on its own; the real tell is a property of a
  *sequence*, not a snapshot.
- We tried to recover some of that temporal signal cheaply — rolling mean/std/rate-of-change
  features computed from the existing per-frame CSV, no new video extraction — and it **made
  results worse** (36.7%, and 29.8% with SMOTE on top), not better. Best guess why: a rolling
  *mean* over a short single-label clip converges toward that clip's own average value, which
  reads more like a per-subject/per-clip fingerprint than genuine moment-to-moment dynamics — it
  handed an already overfitting-prone model an even easier shortcut, rather than new information.

Further RandomForest/Dense NN tuning is paused (see `CLAUDE.md` for the full record, including
why the RF hyperparameter search notebook cell hangs — a `RandomizedSearchCV`/`RandomForestClassifier`
double `n_jobs=-1` nesting issue, not a logic bug).

**The CNN's first run produced an 84.75% test accuracy that was never trustworthy — a degenerate-
split, majority-class-collapse artifact, not a real result. That run has since been superseded by
a real one, and the honest number is much lower:**

- `face_crops_index.csv` (from `06`) originally covered only **6 subjects** — the original Argus
  recordings, not yet the extracted UTA-RLDD ones (`subject_07+`). An 80/20
  `GroupShuffleSplit(random_state=42)` over 6 subjects put 2 subjects in the test fold, and those 2
  happened to have **zero `Alert` crops between them**. Against that degenerate test set, the model
  scored 84.75% accuracy purely by defaulting to `Low Vigilant` (89% of the test set): 0.00 recall
  on `Drowsy` (93 test examples, all missed) and `Alert` had no test examples to even score
  against. Training accuracy also hit >99% within 2 epochs on a 28K-param model trained over only
  4 subjects — a sign the model was keying on subject identity (skin tone/lighting/background)
  rather than drowsiness cues, not evidence of a genuinely easy task.
- **The split/metric/augmentation fixes proposed in response to that run** (`StratifiedGroupKFold`
  restricted to choosing test-fold subjects only from among subjects with all 3 classes present,
  macro-F1-based checkpointing instead of raw accuracy, strengthened rotation/zoom/contrast
  augmentation) have since actually been rerun, against the fuller extracted subject set (24
  subjects, 3751 crops, 20 of them complete-class): **31.53% test accuracy, 0.33 macro-F1**
  (`Alert` recall 0.28, `Low Vigilant` 0.25, `Drowsy` 0.42). This is real signal, not a bug in the
  eval code, and it's a generalization-gap problem specifically, not underfitting: train accuracy
  reaches 99%+ by epoch 22, but test loss (5.81) runs nearly 4x the best logged validation loss —
  the val-based guardrails above are real improvements, but with only 20 complete-class subjects
  (16 train / 4 val / 4 test) a single fold's number also carries real sampling variance from
  which specific subjects land in the test split.
- **Further changes added to `07_cnn_training.ipynb` in response to this real result** — a
  MobileNetV2 frozen-ImageNet-backbone variant — previously defined in the notebook but never
  actually trained — now trained and evaluated side by side with the from-scratch CNN, with an
  optional fine-tuning phase (unfreeze the backbone's last ~30 layers, much lower LR); a
  `USE_FOCAL_LOSS` toggle (on by default) as an alternative to plain class-weighted cross-entropy;
  stronger regularization (`l2` now on all three conv layers, not just the head; dropout raised
  0.4 → 0.5); and a subject-fold cross-validation diagnostic (`RUN_CROSS_VALIDATION`) to measure
  how much the reported number moves across different held-out-subject draws rather than trusting
  one fold's number as the ceiling — **these have now been rerun, with real numbers:**
  - The **from-scratch CNN**, with the regularization/focal-loss changes, improved to **36.67%
    test accuracy, 0.3614 macro-F1** on the same single fold (`Alert` recall 0.33, `Low Vigilant`
    0.27, `Drowsy` 0.50) — a real gain over the 31.53%/0.33 figure above. The **3-fold subject-CV
    diagnostic** puts this at **35.93% ± 9.07% test accuracy** (macro-F1 0.3422 ± 0.1085) across
    folds ranging 25.45%-41.23% — confirming real fold-to-fold variance at this subject count, but
    the mean lands close to the single-fold number rather than far from it.
  - The **MobileNetV2 transfer-learning variant was a clear regression, not an improvement**:
    **16.81% test accuracy, 0.1428 macro-F1 frozen**, and fine-tuning the last ~30 backbone layers
    didn't fix it (**16.81% accuracy, 0.1447 macro-F1**) — both variants collapsed to **zero
    recall on `Low Vigilant`**, never predicting that class at all. That specific failure shape (a
    clean 2-of-3-class collapse, not just lower accuracy) points more at a config/architecture
    mismatch than a real "pretraining doesn't help" verdict: `alpha=0.35` (the thinnest MobileNetV2
    variant) at a 96×96 input downsamples to roughly a 3×3 feature map before global-average-
    pooling, which may be discarding most of the spatial detail the eye/mouth region needs. Before
    treating MobileNetV2 as ruled out, a higher input resolution or larger `alpha` is worth trying
    — this hasn't been done yet.
- Both `06` and `07` were also refactored to cut redundant cells (merged setup cells, consolidated
  the "Index CSV Summary" and "Dataset Sanity Check" sections into one, and the "Recovering an
  Index" utility in `06` dropped its legacy `_f{frame_idx}`-filename branch — the pipeline has
  exclusively written `_s{sample_idx}` filenames for a while, so that branch was dead weight for
  any future recovery).
- **The real fix is still more subjects — and this has now moved forward again.** `raw_videos/`
  on Drive has continued growing via `extract_uta_rldd_clips.py`, up to roughly **54 subjects**
  as of this writing — well past the 24 subjects (20 complete-class) `06`/`07`/the first `10` run
  below actually indexed. `06` has **not been rerun** against this larger pool yet (it also needs
  a `reset_dataset=True` rebuild regardless, not just a resume — see `06`'s
  `MAX_FRAMES_PER_CLIP` change above), so `face_crops_index.csv`/`cnn_lstm_windows_index.csv`
  are both still stale relative to what's actually on Drive. Rerunning `06` (reset) → `09` → `10`
  against the full subject pool is the next concrete step, not a hypothetical one.

**`10_cnn_lstm_training.ipynb`'s first real run — against the stale 24-subject window index, not
yet the 54-subject one, so treat this as a pipeline-correctness smoke test more than a verdict:**

| Variant | Test Acc | Macro-F1 | Alert R | Low Vigilant R | Drowsy R |
|---|---|---|---|---|---|
| CNN+LSTM (from-scratch) | 34.19% | 0.1936 | 0.99 | 0.03 | 0.01 |
| CNN+LSTM (MobileNetV2, frozen) | 46.79% | 0.4090 | 0.83 | 0.00 | 0.57 |
| CNN+LSTM (MobileNetV2, fine-tuned) | 43.38% | 0.3827 | 0.81 | 0.00 | 0.49 |

- **The MobileNetV2 resolution/alpha fix (`alpha=1.0`, `IMG_SIZE_MOBILENET=128`, vs. 07's
  `alpha=0.35`/96×96) looks like it worked** — 46.79% accuracy / 0.4090 macro-F1 (frozen) is a
  real jump over 07's single-frame MobileNetV2 (16.81%) and over every other model tried so far,
  including this notebook's own from-scratch variant. Real, useful evidence for the diagnosis in
  "Cheaper options" above, not yet a validated production number.
- **The from-scratch backbone got *worse*, not better, than 07's single-frame CNN** (0.1936
  macro-F1 vs. 07's 0.3614) — it collapsed toward predicting `Alert` almost exclusively (0.99
  recall there, ~0 on the other two classes). Its saved checkpoint was also picked at a
  `val_macro_f1` of 0.48, but scored 0.19 macro-F1 on the actual test set — a large val/test gap
  driven by a tiny (117-window, 4-subject) validation pool, a genuine reliability problem at this
  subject count, not just a modeling one.
- **`Low Vigilant` recall is exactly 0.0000 for *both* MobileNetV2 variants** — never predicted
  once. Same failure shape as 07's MobileNetV2 collapse, just landing on a different class this
  time, which looks less like a fixed bug and more like `Low Vigilant` (the ambiguous middle
  class) being structurally the hardest for every backbone tried so far. Worth watching closely
  on the 54-subject rerun — if it persists there too, that's a stronger signal than "not enough
  data."
- **Fine-tuning made MobileNetV2 worse, not better** (46.79%→43.38% accuracy, 0.4090→0.3827
  macro-F1) — its best epoch was epoch 1 of fine-tuning; every epoch after degraded further.
  Worth reconsidering whether the fine-tune phase helps at all at this dataset size before
  assuming it just needs a longer run.
- **No evidence yet that the extra temporal context helps** — the whole premise of building this
  model. Per-duration accuracy is flat for MobileNetV2 frozen (46.8/46.5/47.2/47.2% across
  3s→20s) and trends slightly *down* with longer windows for the fine-tuned variant. Not a
  result to read too much into given the subject count, but not a positive signal either.

**`10` has since actually been rerun against the full 54-subject window index (48 complete-class
subjects; Train 4359 / Val 234 / Test 1170 windows) — and the picture changes substantially, not
in the direction the 24-subject smoke test suggested:**

| Variant | Test Acc | Macro-F1 | Alert R | Low Vigilant R | Drowsy R |
|---|---|---|---|---|---|
| CNN+LSTM (from-scratch) | 29.83% | 0.2801 | 0.35 | 0.43 | 0.12 |
| CNN+LSTM (MobileNetV2, frozen) | 33.68% | 0.3340 | 0.44 | 0.27 | 0.29 |
| CNN+LSTM (MobileNetV2, fine-tuned) | 33.68% | 0.3130 | 0.31 | 0.56 | 0.14 |

- **The 24-subject run's headline 46.79%/0.4090 (MobileNetV2, frozen) does not hold up** — on the
  full pool it drops to 33.68%/0.3340, now *below* both Dense NN (38.6–40.8%) and 07's
  single-frame CNN (36.67%/0.3614), not above them. Treat the 24-subject number as exactly what it
  was labeled: a pipeline-correctness smoke test, not a real verdict.
- **One genuine improvement, though: the `Low Vigilant` zero-recall collapse is gone.** Both
  MobileNetV2 variants now score real (non-zero) `Low Vigilant` recall (0.27 frozen, 0.56
  fine-tuned) instead of the flat 0.0000 both had at 24 subjects — consistent with "not enough
  data for this class" being a real contributor, on top of whatever the resolution/alpha fix
  addressed.
- Per-duration accuracy is still flat (frozen MobileNetV2: 3s .330 / 5s .344 / 10s .339 / 20s
  .344) — still no evidence the extra temporal context is paying for itself.
- **This table is itself now superseded by an architecture change, not just a subject-count
  change.** `09`/`10` were subsequently modified to (a) fuse a 10-feature EAR/MAR/blendshape
  vector (extracted via `FaceLandmarker` run directly on `06`'s saved crops, not a `06` re-run —
  see `09`'s "Geometric Features for Fusion" section) into the per-timestep CNN embedding before
  the LSTM, (b) lower the initial learning rate (`0.001` → `1e-4`, tightened again from an
  intermediate `3e-4` pass after that run's log showed clear overfitting -- also
  `ReduceLROnPlateau`'s `patience` `8` → `4`), and (c) an asymmetric
  class-weight scheme (`Drowsy` boosted `1.5x`; `Low Vigilant` dampened to `0.5x` of its balanced
  weight — see `10`'s "CNN+LSTM Training (From-Scratch Backbone)" section for the full
  rationale). None of the numbers in either table above reflect that architecture — both
  notebooks' stale outputs from before this change were cleared rather than left attached to code
  that no longer matches them. Re-running `09` (to populate `geometric_feature_seq`) then `10` is
  needed before this section can report a real number for the fused model.
- **Diagnostic finding from two actual training-log reads of the fused model (not yet a full
  evaluated result): learning rate was ruled out as the cause of its overfitting pattern.** Both
  the `3e-4` pass and the `1e-4` pass (the latter with `ReduceLROnPlateau` cutting it down to
  ~1.25e-5 over the run) converged to the *same* shape: `val_macro_f1` peaks early (epoch ~3-5,
  ~0.40-0.46) then degrades steadily for the rest of the run while train accuracy keeps climbing
  smoothly through every LR cut. Three very different learning-rate values hitting the same early
  peak means a smaller LR isn't the lever that raises it. **Correction to an earlier version of
  this note:** it previously claimed `USE_CLASS_WEIGHTS` already existed as a toggle in `10` —
  that was wrong; as of that writing the notebook still applied `class_weight` unconditionally in
  all three `.fit()` calls, with no toggle at all. `USE_CLASS_WEIGHTS` (default `False`, meaning
  `class_weight=None` — genuinely uniform, not just the asymmetric boost/dampen turned off on top
  of `'balanced'`) has now actually been added to `10`'s class-weight cell, to isolate whether the
  asymmetric class-weight skew itself, combined with a small `BATCH_SIZE=8`, is contributing to
  how noisy `val_macro_f1` is epoch to epoch — untested as of this writing (no rerun yet). Two
  more changes landed in the same pass, targeting capacity/regularization instead of LR (per the
  finding above that LR isn't the lever): `AdamW`'s decoupled weight decay (`WEIGHT_DECAY = 1e-4`)
  replaces plain `Adam` + `l2(0.001)` scattered across every conv/dense layer in both backbones'
  feature extractors, and the `LSTM`'s `recurrent_dropout=0.3` was added on top of its existing
  `dropout=0.3` — free here specifically, since `use_cudnn=False` was already forced for an
  unrelated pre-padding reason, so the usual cuDNN-fast-path cost of `recurrent_dropout` doesn't
  apply. `learning_rate` itself stays at `1e-4`, deliberately not lowered further — see `10`'s
  `build_cnn_lstm` docstring for the full reasoning against that. **Worth noting regardless of
  outcome: the late-run decline in either prior pass never reached the actual saved/evaluated
  model** — `ModelCheckpoint(save_best_only=True, monitor='val_macro_f1')` keeps only the best
  epoch, so training past the peak wastes compute but doesn't corrupt the result.

**That change has now actually been run, with real numbers — and MobileNetV2 has been disabled as
a result, not just deprioritized:**

- **From-scratch backbone: 35.04% test accuracy, 0.3296 macro-F1** (`Alert` recall 0.57,
  `Low Vigilant` 0.14, `Drowsy` 0.34) — a real improvement over the same backbone's prior run
  without `AdamW`/`recurrent_dropout`/`class_weight=None` (29.83% / 0.2801), and now close to
  (though still just under) `07`'s single-frame CNN (36.67% / 0.3614). `val_macro_f1` still
  peaked early in training (epoch 5, 0.4182) and declined afterward — the same shape as every
  prior run — but the peak itself and the final test number both moved up, so the regularization
  changes did something real even though they didn't eliminate the early-peak pattern.
  **Within this single run, `ReduceLROnPlateau`'s own decay produced direct evidence against a
  lower learning rate**: as it cut `1e-4` down to `6.25e-6` by epoch 17-20, `val_macro_f1` at that
  lowest LR was ~0.22-0.23 — worse than at every higher LR earlier in the same run, not better.
  **In response, `ReduceLROnPlateau` has been replaced with `CosineDecayRestarts` (SGDR) for the
  from-scratch model**, which decays smoothly within each cycle but jumps the LR back up at each
  restart instead of only ever shrinking it (`ReduceLROnPlateau` doesn't compose with a
  `LearningRateSchedule` object, so it's removed rather than combined with the new schedule).

**That schedule has since actually been run once (5-epoch first cycle, `t_mul=2.0`, `m_mul=0.9`,
`alpha=0.05`), then tuned twice more based on what it showed:**

- **The first run validated the restart idea directly: `val_macro_f1` hit 0.4618 at epoch 6 — the
  best result across every run of this notebook so far — right at the first restart.** But by
  ~epoch 10 (4-5 epochs later) it had already re-overfit back to ~0.20-0.29, and the second
  restart (~epoch 15, that cycle being 10 epochs under `t_mul=2.0`) produced a much weaker bump —
  by then the model had dug itself into a harder-to-escape region. Restarts help, but needed to
  happen closer to how fast this model actually re-overfits (~4-5 epochs), not every 5→10→20.
- **Current schedule (not yet run), deliberately aggressive:** `first_decay_steps` = 2 epochs
  (down from 5), `t_mul=1.15` (cycles barely grow — restarts stay frequent through the whole run),
  `m_mul=1.0` (every restart is a full reset to `1e-4`, no cooldown), `alpha=0.02`.
- **Regularization raised alongside it, same evidence, same request** (twice, culminating in
  genuinely high values): `WEIGHT_DECAY` `1e-4 → 3e-4 → 6e-4`; `LSTM_DROPOUT`/
  `RECURRENT_DROPOUT` `0.3 → 0.4 → 0.5`; `HEAD_DROPOUT` (the LSTM's final `Dropout`, now a named
  constant) `0.5 → 0.6 → 0.7`. **Worth watching on the next run: if train accuracy also stays
  low/flat this time (not just val), that's a sign this overshot into underfitting** — the fix
  then is backing these off, not pushing them higher again.
- **A from-scratch training run with the second-round settings (`WEIGHT_DECAY=3e-4`,
  `LSTM_DROPOUT`/`RECURRENT_DROPOUT=0.4`, `HEAD_DROPOUT=0.6`, `first_decay_steps`=3 epochs,
  `t_mul=1.3`) reached `val_macro_f1` **0.6799 at epoch 28** (`val_accuracy` 0.6880) — but that
  number has now been checked against the test set, and the explicit caveat below is confirmed,
  not resolved.** **Test result: 32.22% accuracy, 0.3251 macro-F1** (`Alert` P/R 0.42/0.31,
  `Low Vigilant` 0.26/0.34, `Drowsy` 0.33/0.32) on a perfectly balanced 390/390/390 test set —
  i.e. at, if not fractionally below, the 33.3% random-guess baseline for 3 balanced classes.
  Per-duration accuracy is flat and equally uninformative (32.0/31.9/33.3/32.2% at
  3s/5s/10s/20s). The epoch-28 `val_macro_f1` spike was validation-set luck from a 234-window
  validation pool, exactly as flagged when it was first observed — it did not survive contact
  with the untouched 1170-window test set. The training log itself (cell 14) also cuts off
  mid-epoch 40 without reaching `EarlyStopping`'s patience-15 stop or the 100-epoch cap, so this
  specific run didn't even run to completion; the picked checkpoint is still the epoch-28 best
  by `val_macro_f1`, so that doesn't change the test number above. Given that every
  regularization change tried so far (`AdamW` weight decay `1e-4→3e-4→6e-4`, LSTM
  dropout/recurrent_dropout `0.3→0.4→0.5`, head dropout `0.5→0.6→0.7`, focal loss,
  `USE_CLASS_WEIGHTS` toggled off, `ReduceLROnPlateau`→`CosineDecayRestarts`) has left the
  train/test gap intact — train accuracy still climbs past 70% while test performance sits at
  chance — this reads as a subject-count ceiling for an end-to-end-trained CNN+LSTM, not an
  undiscovered hyperparameter. The untested third-round regularization settings
  (`WEIGHT_DECAY=6e-4`, dropout `0.5`/`0.5`/`0.7`, 2-epoch restart cycles) are deprioritized as a
  result — see "Working in this directory" for the recommended next step instead of continuing
  to tune this notebook.
- **MobileNetV2 (frozen head): severe overfitting, worse than any prior run of it.** 95%+ train
  accuracy by epoch 6, `val_macro_f1` never exceeding 0.36 and mostly sitting in 0.24-0.33,
  trending worse rather than converging. The run was stopped before the fine-tune/evaluation
  cells ran — the frozen-head log alone was conclusive enough not to justify the compute. This is
  consistent with, and worse than, every previous MobileNetV2 result in this project (07's
  single-frame MobileNetV2 collapse to 16.81%/zero `Low Vigilant` recall; this notebook's own
  earlier pre-fusion run putting MobileNetV2 frozen at 33.68%, still below from-scratch's 29.83%
  in that same run). MobileNetV2 has never once beaten the from-scratch backbone on a trustworthy
  run in this project. **`10_cnn_lstm_training.ipynb`'s MobileNetV2 cells (frozen training,
  fine-tune, three-way comparison) are now commented out** — not deleted, kept as a documented,
  re-enable-able option, but no longer part of the notebook's default run.

**`10` has since been run for the first time under the actual binary label scheme (`06`
rebuilt against `raw_videos_binary/` at `sampling_fps=5`; `09` rerun against that rebuild — 5731
windows, 54 subjects, 50 complete-class, real `geometric_feature_seq` fusion present). This
supersedes every 3-class number above for this notebook. Trained with `USE_CLASS_WEIGHTS=False`
(uniform loss — the same isolation test described earlier in this section), against a test set of
775 Not Drowsy / 394 Drowsy windows (a 66.3%-majority-class baseline, macro-F1 ≈ 0.40 for
always-predicting-majority):**

| Variant | Test Acc | Macro-F1 | Not Drowsy R | Drowsy R | Trainable params | Time/epoch |
|---|---|---|---|---|---|---|
| CNN+LSTM (from-scratch) | 69.89% | 0.5762 | 0.93 | 0.24 | 63,682 | ~206s |
| CNN+LSTM (frozen CNN embedding + LSTM) | 67.07% | **0.6213** | 0.78 | **0.46** | 35,714 | **~2s** |

- **Both clear the majority-class baseline** (macro-F1 0.40), so there's real signal here, not
  pure majority-class collapse — but the from-scratch variant's accuracy (69.89%) sits close
  enough to the 66.3% floor, combined with its lopsided recall (0.93/0.24), that most of its
  apparent accuracy is coming from leaning on the majority class rather than genuinely
  discriminating `Drowsy`. Its `val_macro_f1` curve confirms this: peaks at epoch 1 (0.6077),
  then decays to the 0.40-0.54 range for the next 15 epochs — the same early-peak-then-decline
  shape as every 3-class run of this backbone, just on new data.
- **The frozen-CNN-embedding variant (option 3 from "Cheaper options" below) is the better result
  of the two, not merely the cheaper one.** Higher macro-F1, nearly double the `Drowsy` recall
  (the safety-critical number — a driver-monitoring system's whole job is not missing this), far
  less overfitting (`val_macro_f1` peaks at 0.7311 (epoch 2) and stays in 0.70-0.73 through epoch
  17, instead of collapsing), half the trainable parameters, and roughly 100x cheaper per epoch.
  This is the first real evidence that option 3's premise — reusing a frozen single-frame CNN's
  embedding is enough, an end-to-end-trained CNN backbone inside the LSTM loop isn't necessary —
  actually holds, not just a cost-saving compromise.
- **Caveat that applied at the time: `07` had not been retrained against the rebuilt binary `06`
  crop set yet**, so this run's frozen feature extractor was still `07`'s old, pre-binary-
  migration checkpoint. That caveat is now resolved — see the next subsection, where both `07`
  and `11_drive_pull` have been rerun on consistent binary data and the result is superseded by a
  much stronger one.
- **Per-duration accuracy is still flat in both variants** (from-scratch: 0.697-0.705 across
  3s/5s/10s/20s; frozen-embedding: 0.669-0.676) — still no evidence that a *longer* window buys
  anything over a shorter one, the same finding as every prior run of this notebook and of `01`'s
  geometric LSTM. (The flatness itself holds in the superseding run below too — 0.839-0.856
  across the same four durations — but at that much higher level it reads differently: not "no
  evidence temporal context helps" but "a short window already captures whatever signal a longer
  one does," which is good news for a low-latency deployment. See that subsection.)
- **`USE_CLASS_WEIGHTS` has since been flipped to `True`** (`DROWSY_WEIGHT_BOOST=1.5`, unchanged)
  in the notebook, specifically to test whether reweighting raises the frozen-embedding variant's
  0.46 `Drowsy` recall further — **not yet run**. Don't report a weighted number for either
  variant until that run actually happens; the table above is the uniform-weight baseline.

## `11_cnn_lstm_training_drive_pull.ipynb`'s fixed rerun — new best result in the project

The LR bug described in the `11_…` section below is fixed, `09` has been rerun with minority-
class window overlap-tiling active (near-balanced train split: 2270 Not Drowsy / 1900 Drowsy
windows), and `07` has been retrained on the rebuilt binary `06` crop set (see the pipeline map's
`07` row: **59.64% acc / 0.5273 macro-F1** single-frame, on the same 10 held-out subjects used
below). With all three of those pieces now consistent and binary, `drive_pull` was rerun for
real — not the `upload` sibling, and not the earlier `10` run on stale/pre-rebalancing data. Test
set: 780 Not Drowsy / 660 Drowsy windows (54.2%-majority-class baseline, macro-F1 ≈ 0.47 for
always-predicting-majority), 10 held-out subjects, same `split_by_subject_fold` as `07`:

| Variant | Test Acc | Macro-F1 | Not Drowsy R | Drowsy R | Trainable params |
|---|---|---|---|---|---|
| CNN+LSTM (from-scratch) | 62.29% | 0.6078 | 0.76 | 0.47 | — |
| CNN+LSTM (frozen CNN embedding + LSTM) | **84.24%** | **0.8375** | 0.94 | 0.73 | 35,714 |
| Ensemble (0.5·scratch + 0.5·frozen) | 82.99% | 0.8255 | 0.91 | 0.73 | — |

- **The frozen-CNN-embedding + LSTM variant is now the best measured result in the entire
  project, by a wide margin** — nearly double `07`'s own single-frame macro-F1 (0.5273 → 0.8375)
  from the *same* CNN's embeddings, just aggregated over a window by the LSTM instead of judged
  frame-by-frame. This is the first time in ten notebooks that the project's central temporal-
  context hypothesis (a sequence reveals what a single frame structurally can't, per "Why CNN is
  the most probable next backbone" below) is actually confirmed by a clean, well-behaved number,
  not just argued for.
- **At the chosen safety threshold** (`t*=0.57`, picked on validation by `DROWSY_RECALL_FLOOR`):
  84.38% acc, 0.8379 macro-F1, Drowsy P 0.93/R 0.71 — val→test transfer gap is tiny (val
  macro-F1 0.8458-0.8509 vs. test 0.8375-0.8386), a healthy sign, not validation-set luck.
- **Verified not a repeat of the earlier `drive_pull` LR bug or the 84.75% degenerate-split
  artifact**: the epoch log shows real learning from epoch 1 (`val_macro_f1` climbing off a
  sensible base, not frozen at an untrained-init value), `val_macro_f1` stays in a stable
  0.76-0.85 band across all 20 trained epochs (not a single early spike that then collapses to
  chance, the shape that sank the from-scratch variant in earlier runs), and the split is the
  same subject-grouped `StratifiedGroupKFold` `07` uses — `11`'s test subjects are the same ones
  `07` itself held out, so the frozen CNN never saw those faces during its own training either.
- **The from-scratch backbone also improved substantially** (62.29% acc / 0.6078 macro-F1, vs.
  0.5762 in the earlier pre-rebalancing `10` run) but stays well behind the frozen-embedding
  variant, consistent with every prior run of this notebook — an end-to-end-trained CNN inside
  the recurrent loop keeps underperforming the cheaper frozen-embedding approach at this subject
  count.
- **The ensemble is worse than the frozen-embedding model alone** (0.8255 vs. 0.8375 macro-F1) —
  the weaker, noisier from-scratch probabilities drag the average down. Deploy the
  frozen-embedding model by itself, not the ensemble.
- **Real caveats, not yet resolved by this run:**
  - **Single fold, no cross-validation yet.** This is one `StratifiedGroupKFold` split (10 held-
    out subjects out of 50 complete-class). The project's own 3-fold subject-CV diagnostics
    elsewhere (`07`'s single-frame CNN) have shown ±9-point macro-F1 swings at similar subject
    counts — this specific 0.8375 hasn't been stress-tested that way.
  - **Test-set Drowsy windows aren't fully independent samples.** Minority-class window
    overlap-tiling (50% stride) happens before the subject split, so some of the 660 "Drowsy"
    test windows are staggered, correlated views of the same clip — no leakage (still
    subject-disjoint), but the effective independent sample size is smaller than 660 implies, so
    treat the confidence interval as wider than the raw N suggests.
  - **Not yet run with `USE_CLASS_WEIGHTS=True`** or on the `upload` sibling for a same-config
    comparison — the jump from the earlier 0.6213 macro-F1 is best explained by the combination
    of the LR fix, the retrained `07` checkpoint, and the now-active window rebalancing, not a
    single isolated cause.
  - **Now deployed, but still not run end to end against real hardware.** `src/cv-argus`
    replaced its earlier single-frame-CNN default with this pipeline shape (frozen CNN embedding
    + geometric-feature fusion + LSTM over a rolling window) — see the root `CLAUDE.md`'s
    "Current deployment status". Its mechanics are smoke-tested, but a real run against real Pi
    hardware, and the CNN-checkpoint-provenance check, are still open — see
    `src/cv-argus/CLAUDE.md`'s "Current status" before treating this as a validated production
    result.

## `11_cnn_lstm_training_{upload,drive_pull}.ipynb` — Drive-I/O variants of `10`, and a push on Drowsy P/R

`10`'s `tf.data` image pipeline reading thousands of small crop JPEGs over Drive's FUSE mount is
what threw `Input/output error` / `A Google Drive timeout has occurred`. `11_cnn_lstm_training_upload.ipynb`
and `11_cnn_lstm_training_drive_pull.ipynb` are siblings of `10` that fix only *how the crops
reach the Colab VM* — `upload` via a hand `files.upload()` of a zip each session, `drive_pull`
by copying one `face_crops.zip` off Drive and unzipping to local disk. Everything downstream (the
split, both model variants, training, eval) is meant to be identical to `10`. **The earlier
genuine binary frozen-embedding result (0.6213 macro-F1, 0.46 `Drowsy` recall) came from the
`upload` variant** — that number is now superseded by `drive_pull`'s fixed rerun below (0.8375
macro-F1), which benefits from a retrained `07` checkpoint and active window rebalancing that
the `upload` run didn't have; `upload` hasn't been rerun under the same conditions for a
same-config comparison.

**A learning-rate bug was found in `drive_pull` — now fixed and confirmed working.** It had
`lr_schedule_scratch`'s `CosineDecayRestarts(initial_learning_rate=1e-8, …)` (that argument is
the schedule's *peak* LR) and `build_frozen_embedding_lstm(learning_rate=1e-8)` called with no
override — so **both models trained at ~1e-8 and did not learn**: the from-scratch model's train
accuracy sat flat at ~0.50 for every epoch, and the frozen-embedding model's `val_accuracy` /
`val_macro_f1` were frozen at the untrained-init values (`0.7511` / `0.7151`) for the whole run
while its real test macro-F1 was 0.50. Any "still overfitting after round-2 regularization"
reading from that early `drive_pull` run was not real — the model wasn't training. Fix:
`initial_learning_rate=1e-4`, frozen builder `learning_rate=1e-3`, and the drive-pull-only
"round 2" regularization bump (`WEIGHT_DECAY` `1e-4→3e-4`, LSTM dropout/recurrent `0.3→0.4`, head
`0.5→0.6`, `DROWSY_WEIGHT_BOOST` `0.99`, `NOT_DROWSY_WEIGHT_DAMPEN` `1.225`) reverted to the
round-1 set the `upload` sibling uses. **`drive_pull` has since been rerun with the fix and
produced the project's new best result — see "`11_cnn_lstm_training_drive_pull.ipynb`'s fixed
rerun" above.**

**New machinery added to both `11` notebooks and to `07`, aimed at `Drowsy` precision+recall
(none of it rerun yet):**
- **Decision-threshold / operating-point cells** (new, after each eval). Everything was `argmax`
  at 0.5; these sweep the threshold on `p(Drowsy)` on the *validation* set and pick a
  *safety-first* point — among thresholds with val `Drowsy` recall ≥ `DROWSY_RECALL_FLOOR`
  (default 0.70), the best `Drowsy` precision — then report test metrics there next to the argmax
  baseline, plus an isotonic-calibration transfer check. The chosen raw-probability threshold is
  written to `<checkpoint>.keras.threshold.json` for `src/cv-argus` to apply instead of `argmax`.
  When reporting `11`/`07` results, don't quote the argmax numbers as final — quote the number at
  `t*`.
- **Balanced resampling** (`USE_BALANCED_SAMPLING`, default `True`). The training set is drawn
  50/50 from the two classes (`tf.data.Dataset.sample_from_datasets` over two per-class
  `.repeat()` streams; `07` splits the dataframe by label, `11` filters per class off separate
  cache files), with `class_weight=None` and an explicit `steps_per_epoch`. Preferred over
  `class_weight` at `11`'s `BATCH_SIZE=8`, where one reweighted example can dominate a batch.
- **Cheap ensemble cell** in `11` — averages `p(Drowsy)` of the from-scratch and frozen-embedding
  models (same `df_test` order) and runs the same threshold sweep on the average.
- **`07`**: from-scratch CNN LR raised `1e-5 → 1e-4`; the `Drowsy` class-weight knobs, which had
  been set to *tilt away* from `Drowsy` (`DROWSY_WEIGHT_BOOST=0.9`, `NOT_DROWSY_WEIGHT_BOOST=1.375`),
  corrected to `1.5` / `1.0`; `MacroF1Callback` also logs `val_thr_macro_f1` (macro-F1 at the
  best per-epoch threshold) as an optional checkpoint-selection metric.

**Minority-class window rebalancing (Part 6 — code done in `src/dataset`, `09` mirrored, not
rerun).** The ~2:1 Not Drowsy : Drowsy clip ratio carries straight into the window index under
`09`'s non-overlapping tiling. `src/dataset/argus_dataset/config.py` now has
`CNNLSTM_MINORITY_LEVEL = 2` / `CNNLSTM_MINORITY_WINDOW_OVERLAP = 0.5`: `cnn_lstm_windows_for_clip`
takes a `window_overlap` arg, and the driver passes it for `level_2` clips only, so Drowsy
windows are overlap-tiled (~half stride) while Not Drowsy stay non-overlapping — lifting the
window-level Drowsy share toward parity without new video. `09` mirrors this
(`MINORITY_WINDOW_OVERLAP`, per-class tiling spot-check). Subject-grouped splitting stays
leak-safe (split is by subject). Regenerating `cnn_lstm_windows_index.csv` and rerunning `11` is
the pending step. `0.0` restores the historical behaviour.

## Why CNN is the most probable next backbone

The diagnosis above is specific: it's not "RandomForest and Dense NN are weak models," it's "a
single frame's **hand-engineered geometric summary** (EAR/MAR/blendshape scores) doesn't carry
enough signal." That points at two independent levers, both still untried:

1. **Raw pixels carry information the geometric features never captured at all** — skin texture,
   subtle redness around the eyes, micro-expression detail, asymmetries a 51-category blendshape
   vocabulary has no slot for. A CNN sees the actual image, not a hand-picked summary of it, so
   it isn't limited to the same |r| ≤ 0.26 ceiling by construction — it might find signal the
   geometric approach structurally couldn't represent, even from a single frame (`07_cnn_training.ipynb`).
2. **Real temporal context, combined with that pixel-level view** — `09_dataset_creation_cnn_lstm.ipynb`
   already builds the dataset index for a `TimeDistributed(CNN) → LSTM` hybrid: the same
   windowed-sequence idea that makes the geometric LSTM work (curated 3s/5s/10s/20s durations,
   tiled non-overlapping within each duration — see the root `CLAUDE.md`'s "Notebook
   architecture" for why this diverges from the geometric LSTM's own 1-6s/1s-stride scheme),
   applied to face-crop *images* instead of feature vectors. This is the most complete answer to
   the diagnosed problem, since it removes both limitations (single-instant *and*
   hand-engineered-summary) at once, not just one.

**Calibrated expectation, not a promise:** this is also, by a wide margin, the most data-hungry
architecture in this project — a deep model over raw pixels (or pixel *sequences*), trained on a
subject pool that's now 20 complete-class for the CNN path (see "What we found" above), out of up
to 60 available via UTA-RLDD. The real subject-grouped `07` rerun (36.67% accuracy, 0.3614
macro-F1 single-fold; 35.93% ± 9.07% across the 3-fold subject-CV diagnostic) confirms this
direction has real signal but is not yet a clear win — it's now roughly in line with Dense NN's
38.6-40.8%, and clearly above RandomForest's 32.6%, but still in the same 33-41% band, not a
decisive break above it. Point 1 above (raw pixels) has now been genuinely tested, not just
proposed — including the MobileNetV2 pretrained-backbone variant, which underperformed sharply
(16.81% accuracy, collapsing to zero recall on `Low Vigilant`; see "What we found" for why that
result looks more like a resolution/architecture mismatch than a genuine verdict against
pretraining). Point 2 (`09`'s windowed CNN+LSTM) is still untested — `09` hasn't been run at all,
and the CNN+LSTM training notebook (`10`) doesn't exist yet.

**This "most probable next backbone" reasoning did become the project's actual direction, and
has since gone a step further**: `src/cv-argus` deployed `07`'s single-frame CNN by default for
a while (36.67% accuracy, 0.3614 macro-F1 single-fold — real signal, but a long way from
validated) before being replaced by the fused CNN-embedding + geometric-feature + LSTM
classifier from `11_cnn_lstm_training_drive_pull.ipynb` (0.8375 macro-F1 on the same held-out
subject shape) — see the root `CLAUDE.md`'s "Current deployment status" and
`src/cv-argus/CLAUDE.md`'s "Current status". Being deployed still doesn't retroactively validate
either number: the fused model is one un-cross-validated fold, and a real Pi-hardware run
hasn't happened yet. Finishing UTA-RLDD extraction toward the full 60-subject pool remains a
real next step regardless — more subjects would firm up any of these numbers.

## Cheaper options considered alongside the full CNN+LSTM build

`09`/`10` (`TimeDistributed(CNN) → LSTM`, see "Why CNN is the most probable next backbone" above)
is the direction actually being pursued as of this writing — both `09_dataset_creation_cnn_lstm.
ipynb` and `10_cnn_lstm_training.ipynb` are now built (two backbones, from-scratch and
MobileNetV2, trained side by side), but `10` hasn't been run yet. It's also, by a wide margin,
the most data-hungry model in the project (see the "Calibrated expectation" note above), so
before proposing a *different* fresh architecture once `10` is run, if it underperforms or
overfits — a real risk, not a hypothetical one, given the subject count, and has since been
confirmed (see "What we found") — check these four already-considered options first; they were
discussed and deliberately not chosen as the primary path, not overlooked:

1. **Diagnose the MobileNetV2 collapse rather than writing pretrained backbones off.** Covered in
   "What we found" above: both the frozen and fine-tuned MobileNetV2 variants collapsed to zero
   recall on `Low Vigilant`, which looks like an `alpha=0.35`/96×96-input resolution mismatch
   (≈3×3 feature map before pooling) rather than a real "transfer learning doesn't help here"
   result. A higher input resolution or larger `alpha` hasn't been tried yet and is a cheap
   (no new data, no new architecture) thing to rule out.
2. **Ensemble the three already-trained single-frame models** (RandomForest, Dense NN, the
   from-scratch CNN) via late fusion (averaged or stacked softmax outputs on the same frame).
   Zero new data and zero new architecture — they cap out at similar accuracy individually but
   draw on genuinely different information (hand-engineered geometric ratios vs. raw pixels), so
   if their errors are even partially uncorrelated, fusing them could beat any single one. Not
   yet tried.
3. **A scaled-down alternative to training a CNN end-to-end inside the recurrent loop — now
   actually built, not yet run.** Instead of `TimeDistributed(CNN) → LSTM` learning a CNN
   backbone from scratch over image sequences (what `10`'s from-scratch/MobileNetV2 variants do),
   a new "Frozen-CNN-Embedding + LSTM Variant" section in `10_cnn_lstm_training.ipynb` (added
   after, not replacing, the existing variants) uses the already-trained `07` CNN as a **frozen**
   feature extractor: its penultimate-layer embedding (64-dim, `GlobalAveragePooling2D` →
   `Dense(64)`) is precomputed once per crop and concatenated with `09`'s existing 10-dim
   `geometric_feature_seq`, then only a small `LSTM(64)` + head trains on top. **One deviation
   from how this option was originally described here worth flagging:** it fuses onto `09`'s
   10-feature face-crop-window geometric set, not `01`'s 58-feature LSTM-window set as first
   proposed — reusing `09`/`10`'s already-built windowing/split infrastructure directly rather
   than building a new `01`-based pipeline from scratch. Still combines raw-pixel visual cues
   with genuine temporal context at far lower trainable-parameter count than end-to-end training,
   which is the part of this option that mattered. Not yet run — next session should read its
   real test result before treating it as validated.
4. **Collapse the 3-class problem to binary, discussed but deliberately deferred behind option 3
   above.** Every model family in this project has been stuck in a 33-41% band on the 3-class
   problem, and `Low Vigilant` (the middle class) has been the single most consistently broken
   class across every architecture tried — not just low-accuracy but literally 0.0000 recall in
   two separate MobileNetV2 runs (07's single-frame version and `10`'s first 24-subject smoke
   test) — which reads as a genuinely hard 3-way discrimination problem, not only a data-count
   one. Merging classes removes that specific failure mode rather than just raising the
   random-guess floor from 33.3% to 50%. Two framings were discussed, not yet chosen between:
   `Alert` vs. `Needs Attention` (merges `Low Vigilant`+`Drowsy`, preserves the early-warning
   framing central to Argus's "preventive layer" positioning — see the root `CLAUDE.md`) vs.
   `Drowsy` vs. `Not-Drowsy` (merges `Alert`+`Low Vigilant`, a purer "is this the dangerous state"
   signal but gives up early warning). **Explicit sequencing decision:** try option 3 above first
   since it's already built and cheap to run; only pursue binary relabeling if the frozen-
   embedding variant's real test result is still stuck near chance, since binary reframing
   touches the label space of every notebook downstream of the raw `level` column, not just `10`.

A fifth, independent lever — worth naming since it's different from "finish extracting UTA-RLDD"
(the currently in-progress data effort) rather than a substitute for it — is **sourcing subjects
from datasets other than UTA-RLDD** (e.g. NTHU-DDD, YawDD, DROZY) once UTA-RLDD's own 60-subject
ceiling is reached. Not evaluated for licensing/label-compatibility yet; a later step, not a
near-term one.

## Recent implementation decisions in `06`/`07`

Three changes landed in these notebooks to address near-duplicate crops and add training-time
augmentation, worth knowing so you don't re-propose them as open problems. (A fourth, larger
round of changes to `07` — the split/metric/augmentation fixes in response to the degenerate
first run — is covered in "What we found" above rather than here.)

- **`06`'s sampling rate: 10 → 1 → 5 FPS.** It was first dropped from 10 to 1 (plus a new
  `MAX_FRAMES_PER_CLIP` cap of 20), on the reasoning that consecutive face crops at a high
  sampling rate are near-duplicate images that add little for a CNN — unlike the
  geometric-feature notebooks, where each frame's small feature vector still carries distinct
  signal even at a high rate. That has since been **partly reversed**: `sampling_fps` is now `5`
  and `MAX_FRAMES_PER_CLIP` is `100` (≈ the first 20 s of each clip), a deliberate
  volume-over-redundancy trade — the CNN / CNN+LSTM path was judged data-starved on the small
  subject pool, and the extra extraction time is absorbed by the parallel worker processes. The
  near-duplication concern is real and acknowledged, not solved; still no similarity/dedup
  algorithm, just the higher fixed-rate-plus-cap. `09` follows `06`'s rate (now also `5`, see the
  pipeline-map rows), so `MAX_TIMESTEPS_IMG` there is `20 * 5 = 100` (was `20`), which multiplies
  `10`'s per-batch image-sequence tensor size 5× — expect to lower `10`'s `BATCH_SIZE`.
- **`06` now writes each CSV row the instant its image is captured**, not once per finished video
  (the previous granularity) or once at the end. Since extraction runs across parallel worker
  processes, this is guarded by a `multiprocessing.Manager().Lock()` shared across workers to
  avoid interleaved rows or duplicate header writes.
- **Correction:** an earlier version of this note claimed `07` augments via Albumentations
  (`RandomBrightnessContrast`/`RandomGamma`/`HueSaturationValue` through `tf.numpy_function`).
  That was never actually in the notebook — the code has only ever used plain `tf.image` ops
  (flip + brightness), now joined by `tf.keras.layers.RandomRotation`/`RandomZoom`/`RandomContrast`
  (see "What we found" above). No Albumentations dependency exists in this notebook; don't assume
  one when reading `07`.
- **MediaPipe's Face Detector (`BlazeFace`) was evaluated against a YOLO-based face detector and
  kept** (this comparison was originally written up as a "Design Decision" markdown cell in `06`
  itself; it was removed from the notebook and lives only here now). Short version: YOLO's
  accuracy edge is on scenarios (cluttered/occluded/wide-angle scenes) this project's
  single-frontal-driver-face setup doesn't have, while it would add real cost here — a heavier
  edge footprint, a second detection framework to maintain alongside the MediaPipe stack the LSTM
  path still needs, and Ultralytics YOLOv8's AGPL-3.0 licensing, a real concern for a project
  meant to become a commercial product.

## One more thing worth knowing before touching `08`

**History:** `MAX_TIMESTEPS` went **120 → 60** (removing unused headroom, after a full extraction
run at 120 OOM-crashed the Colab kernel), then **60 → 30** when `sampling_fps` was cut from 10 to
5 in `01`/`02` (to roughly halve extraction time — a 6s max window is now `6 * 5 = 30` frames).
In notebooks `01`, `03`, and `08`, `MAX_TIMESTEPS`/`max_timesteps` is now **30** (and `08`'s
`LstmGeometricFeatureModel.__init__` default was moved 60 → 30 to match). This whole
windowed-geometric-only LSTM path (`01`→`03`→`08`) was never deployed to `src/cv-argus` — the
project went from nothing deployed, to the single-frame CNN, to the fused CNN-embedding +
geometric-feature + LSTM classifier (see the root `CLAUDE.md`'s "Current deployment status")
without ever shipping this one, and `src/cv-argus`'s corresponding `model/lstm_model.py`/
`detector.py` (`DrowsinessDetector`) were removed once the fused pipeline made this path
obsolete. `08` and its notebooks stay as-is here as a reference/kept-for-history slice of the
pipeline, not as something with a live deployment counterpart to keep in sync anymore.

There is a real, smaller mismatch worth flagging instead: `lstm_model.py`'s
`LstmGeometricFeatureModel.__init__` defaults `num_features` to **59**, but
`03_model_training_lstm.ipynb` *derives* `num_features` from the actual CSV column count and gets
**58** (`GeometricRatioFeatureLayer.blendshape_names` has 51 entries, not the documented 52 —
missing `_neutral` — so 7 geometric features + 51 blendshapes = 58, not 59). Same caveat as the
retracted claim above still applies here, though: a model *loaded* from a saved `.keras` file
gets its real `num_features` from the saved config, not this class default, so this likely isn't
live-broken either. Not fixed here — flagged for whoever picks that up next.

## Working in this directory

- Check the Pipeline map's Status column before assuming a notebook has been run or trustworthy —
  "⬜ not run yet", "⚠️", and "❌ does not exist yet" are load-bearing distinctions, not filler.
  `07`'s reported 36.67% test accuracy / 0.3614 macro-F1 (35.93% ± 9.07% over a 3-fold subject-CV
  diagnostic) is still against the earlier 24-subject pool, even though `06` has since been rerun
  against the full 54-subject one for `09`/`10` — don't report the old 84.75% or 31.53% `07`
  figures as current (both were earlier runs since superseded: 84.75% a degenerate-split
  artifact, 31.53% an earlier real-but-improved-on rerun). `09`/`10` **have** now been run (54
  subjects; see "What we found" for the numbers) — but `10`'s numbers from that run are
  themselves now stale relative to the notebook's current code (geometric feature fusion, lower
  LR, reweighted class weights were added after that run) — don't report either the old
  24-subject smoke-test numbers or the 54-subject pre-fusion numbers as current for `10`. `07`'s
  MobileNetV2 backbone variant has also now been rerun and regressed sharply (16.81% accuracy,
  zero recall on `Low Vigilant`) — treat that as a real result too, not an untested addition, but
  see "What we found" for why it looks more like a resolution/architecture mismatch than a
  verdict against pretrained backbones in general.
- When asked to improve accuracy on the flat dataset (RandomForest/Dense NN), don't propose
  hyperparameter tuning or generic regularization as a first move — re-read "What we found"
  above first. The ceiling is diagnosed as a single-frame information limit (Spearman |r| ≤
  0.26), not an undertuned model, and rolling/temporal feature enrichment was already tried and
  made things *worse*. A new proposal should explain why it addresses the single-instant-in-time
  root cause specifically, not just retry a variant of what's already ruled out.
- When asked what to build next, default to the CNN direction ("Why CNN is the most probable
  next backbone") rather than proposing a fresh architecture search — it's the reasoned next
  step given the evidence here, and notebooks 06/07/09/10 now all exist in support of it (09 as a
  dataset-index step, 10 as the actual training notebook — from-scratch CNN and MobileNetV2
  backbones trained side by side, ported split/loss/callback conventions from 07). `10` has since
  been run (see "What we found" for the 54-subject numbers) and then modified again (geometric
  feature fusion, lower LR, reweighted class weights) — its most recent run's numbers predate
  that modification, so don't report them as current for the notebook's present code either.
- `10`'s **from-scratch** CNN+LSTM confirmed the underperform/overfit case this bullet used to
  treat as hypothetical, on both the pre-migration 3-class data (32.22% accuracy, 0.3251
  macro-F1, at random-guess chance, despite a `val_macro_f1` of 0.6799 mid-training) and now on
  the real binary rerun too (same early-peak-then-decline `val_macro_f1` shape, this time peaking
  at epoch 1 — see "What we found"'s binary subsection). Don't reach for a brand-new architecture
  for that specific backbone, and don't propose further regularization/LR tuning on it as the
  first response — see "Cheaper options considered alongside the full CNN+LSTM build" above.
  **That "cheaper option" (option 3, the frozen-CNN-embedding + LSTM variant) has since been run
  twice under binary labels, and the second run is the project's best result overall, not just
  the best CNN+LSTM one** — `11_cnn_lstm_training_drive_pull.ipynb`'s fixed rerun (retrained `07`
  checkpoint + active window rebalancing) reached **0.8375 macro-F1, 0.73 `Drowsy` recall**,
  superseding the earlier `upload`-sourced 0.6213 figure (see "`11_cnn_lstm_training_drive_pull.
  ipynb`'s fixed rerun — new best result in the project" above for the full numbers and caveats —
  single fold, no cross-validation yet). Treat this as the current best model in the project when
  discussing next steps, not just the best CNN+LSTM variant. **It is now deployed** in
  `src/cv-argus` (replacing the earlier single-frame CNN default), though still not run end to
  end against real hardware — see `src/cv-argus/CLAUDE.md`'s "Current status". The MobileNetV2
  resolution fix and a late-fusion ensemble of the
  existing RandomForest/Dense NN/CNN models are still untried and remain reasonable next things
  to try; the frozen-CNN-embedding hybrid is no longer one of them, since it's now the validated
  best result rather than an untested idea.
- Don't edit `GeometricRatioFeatureLayer` in one of its four copies without checking the other
  three (`01_dataset_creation_lstm.ipynb`, `02_dataset_creation_flat.ipynb`,
  `08_deployment_export_lstm.ipynb`, `src/cv-argus/src/model/layers.py`) — there's no automated
  sync check, per the root `CLAUDE.md`.
- **`07` and `11` now have decision-threshold cells** (after evaluation) that pick a
  `Drowsy` operating point on the validation set and write it to `<checkpoint>.keras.threshold.json`.
  When these notebooks are rerun, the number that matters is the one at the chosen threshold
  `t*`, not the `argmax` (0.5) baseline — don't report the argmax `Drowsy` P/R as the result.
  `DROWSY_RECALL_FLOOR` (default 0.70) is the knob: it's a *safety-first* rule (hit a recall
  floor, then best precision), matching the project decision that missing a drowsy driver is the
  costlier error.
- **`11`'s Drowsy balancing is now `USE_BALANCED_SAMPLING` (50/50 batch resampling), not
  `class_weight`** by default — same for `07`. If you reason about class weights there, check
  which path is active first; with balanced sampling on, `class_weight` is forced to `None` and
  the `DROWSY_WEIGHT_BOOST` constants are dead.
- State findings the way this file does: measured numbers with their source, not aspirational or
  rounded-up claims — this project's own titulación-report standard (see the root `CLAUDE.md`'s
  "Model results and current status") applies to how you describe results here too.
