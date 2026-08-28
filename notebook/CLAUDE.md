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
[`CLAUDE.md`](../CLAUDE.md)'s "Notebook architecture" and "Model results and current status"
sections; this file stays at the level of "what happened and why," not implementation detail —
don't duplicate implementation-level edits' rationale here, put it in the root file instead.

## Pipeline map

| # | Notebook | Reads | Writes | Status |
|---|---|---|---|---|
| 01 | `dataset_creation_lstm` | raw videos | `lstm_windows.csv` | ✅ run |
| 02 | `dataset_creation_flat` | raw videos | `frame_features.csv`, `frame_features_enriched.csv` | ✅ run |
| 03 | `model_training_lstm` | `lstm_windows.csv` | LSTM `.keras` + scaler | ✅ run |
| 04 | `random_forest_training` | `frame_features.csv`/enriched | RF `.joblib` + scaler | ⚠️ baseline run, tuning/augmentation incomplete |
| 05 | `dense_nn_training` | `frame_features.csv`/enriched | Dense NN `.keras` + scaler | ✅ run (multiple passes) |
| 06 | `dataset_creation_face_crops` | raw videos | `face_crops_index.csv` + `.jpg`s | ✅ run against the full 54-subject pool (48 complete-class) as of `09`/`10`'s latest rerun — see "What we found"; the 24-subject/20-complete-class figures elsewhere in this file predate that |
| 07 | `cnn_training` | `face_crops_index.csv` | CNN `.keras` | ⚠️ from-scratch CNN: 36.67% acc / 0.3614 macro-F1 (35.93% ± 9.07% over 3-fold CV), still against the earlier 24-subject pool — low, not a bug; MobileNetV2 backbone rerun too and regressed hard (16.81% acc, collapsed to 0 recall on Low Vigilant) — see "What we found" |
| 08 | `deployment_export_lstm` | LSTM `.keras` + scaler | deployed `LstmGeometricFeatureModel` | ⬜ not re-run since MAX_TIMESTEPS changed to 120 |
| 09 | `dataset_creation_cnn_lstm` | `face_crops_index.csv` | `cnn_lstm_windows_index.csv` | ⚠️ run once against the full 54-subject pool (5763 windows); since then extended to also extract a 10-feature `geometric_feature_seq` (EAR/MAR/blendshapes) per crop for fusion into `10`'s model — needs a rerun to populate that column before `10` can consume it |
| 10 | `cnn_lstm_training` | `cnn_lstm_windows_index.csv` | CNN+LSTM `.keras` (from-scratch only — MobileNetV2 disabled) | ⚠️ latest run (`CosineDecayRestarts`+heavier regularization, geometric fusion): **32.22% test acc, 0.3251 macro-F1 — at random-guess chance** for this balanced 3-class test set, despite a `val_macro_f1` of 0.6799 (epoch 28) that did not hold up on test. Supersedes the 35.04%/0.3296 prior "completed" run as the current number — see "What we found" |

Four model families share two dataset-creation notebooks:

```
01 dataset_creation_lstm  ──► lstm_windows.csv   ──► 03 model_training_lstm ──► 08 deployment_export_lstm
02 dataset_creation_flat  ──► frame_features.csv ──► 04 random_forest_training
                                                  └─► 05 dense_nn_training
06 dataset_creation_face_crops ──► face_crops_index.csv ──► 07 cnn_training
                                                         └─► 09 dataset_creation_cnn_lstm ──► 10 cnn_lstm_training
```

## What we found

**Update since this was first written: the CNN (below) is now what `src/cv-argus` actually
focuses on and deploys by default** (`PIPELINE=cnn`, see `src/cv-argus/CLAUDE.md`'s "Current
status") — the LSTM is no longer the only model family with a working, deployed pipeline. That
doesn't change the architectural reasoning below, though: **the LSTM (windowed geometric
features) remains the intended long-term production model** — see
`03_model_training_lstm.ipynb`'s Design Decision cell for the full reasoning (recurrence over a
buffered window captures duration/velocity of eye closure, which nothing single-frame can see) —
and it doesn't resolve the CNN's own accuracy caveats below either. The CNN's current-deployment
status is a practical decision layered on top of both those facts, not a replacement for either.

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

**This "most probable next backbone" reasoning has since become the current focus, not just a
recommendation**: `src/cv-argus` now actually deploys `07`'s single-frame CNN by default (see
"What we found" above and `src/cv-argus/CLAUDE.md`'s "Current status"). That's a statement about
what's *running*, though, not about what's *validated* — the CNN's real, measured number (36.67%
accuracy, 0.3614 macro-F1 single-fold; 35.93% ± 9.07% across the 3-fold CV diagnostic, small-N
with a real train/test generalization gap) is still a long way from a validated production
result; being deployed didn't retroactively fix that. Finishing UTA-RLDD extraction toward the
full 60-subject pool remains the actual next step, now with more urgency since it's no longer
just a baseline being compared against, but the model this project's edge device runs.

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

- **`06` now samples much more sparsely.** `sampling_fps` dropped from 10 to 1, plus a new
  `MAX_FRAMES_PER_CLIP` cap (20), specifically because consecutive face crops at a high sampling
  rate are near-duplicate images that add little for a CNN to learn from — unlike the
  geometric-feature notebooks, where each frame's small feature vector still carries distinct
  signal even at a high rate. No similarity/dedup algorithm was added; this is a deliberately
  simple fixed-rate-plus-cap approach, not an adaptive sampler.
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

## One more thing worth knowing before touching `08` or `cv-argus/`

**Correction to an earlier version of this note:** it previously claimed `MAX_TIMESTEPS` changed
from 60 to 120 partway through this project's history, and that `src/cv-argus/src/model/
lstm_model.py`'s `max_timesteps: int = 60` default was stale as a result. That had the direction
backwards — checked directly against notebooks 01/03/08's actual cells and `lstm_model.py`:
`MAX_TIMESTEPS`/`max_timesteps` is **60 everywhere, consistently**. It went **120 → 60**
(removing unused headroom, after a full extraction run at 120 OOM-crashed the Colab kernel) — the
same account the root `CLAUDE.md` already gives. There's no live `MAX_TIMESTEPS` inconsistency to
fix here.

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
- `10`'s full CNN+LSTM has now confirmed the underperform/overfit case this bullet used to treat
  as hypothetical — its latest run tested at 32.22% accuracy / 0.3251 macro-F1, at random-guess
  chance for the balanced 3-class test set, despite a `val_macro_f1` of 0.6799 mid-training (see
  "What we found"). Don't reach for a brand-new architecture, and don't propose further
  regularization/LR tuning on this notebook as the first response — see "Cheaper options
  considered alongside the full CNN+LSTM build" above. The MobileNetV2 resolution fix, a
  late-fusion ensemble of the existing RandomForest/Dense NN/CNN models, and a
  frozen-CNN-embedding-into-the-geometric-LSTM hybrid were all already discussed as lower-data-
  cost alternatives; treat them as the next things to try, not as ideas that still need to be
  proposed from scratch.
- Don't edit `GeometricRatioFeatureLayer` in one of its four copies without checking the other
  three (`01_dataset_creation_lstm.ipynb`, `02_dataset_creation_flat.ipynb`,
  `08_deployment_export_lstm.ipynb`, `src/cv-argus/src/model/layers.py`) — there's no automated
  sync check, per the root `CLAUDE.md`.
- State findings the way this file does: measured numbers with their source, not aspirational or
  rounded-up claims — this project's own titulación-report standard (see the root `CLAUDE.md`'s
  "Model results and current status") applies to how you describe results here too.
