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
| 06 | `dataset_creation_face_crops` | raw videos | `face_crops_index.csv` + `.jpg`s | ⚠️ run, but only against the original 6 subjects — not yet rerun against the extracted UTA-RLDD subjects |
| 07 | `cnn_training` | `face_crops_index.csv` | CNN `.keras` | ⚠️ baseline run, but on a degenerate split — see "What we found" |
| 08 | `deployment_export_lstm` | LSTM `.keras` + scaler | deployed `LstmGeometricFeatureModel` | ⬜ not re-run since MAX_TIMESTEPS changed to 120 |
| 09 | `dataset_creation_cnn_lstm` | `face_crops_index.csv` | `cnn_lstm_windows_index.csv` | ⬜ built, not run (needs 06 first) |
| — | `10_cnn_lstm_training` | `cnn_lstm_windows_index.csv` | CNN+LSTM `.keras` | ❌ does not exist yet |

Four model families share two dataset-creation notebooks:

```
01 dataset_creation_lstm  ──► lstm_windows.csv   ──► 03 model_training_lstm ──► 08 deployment_export_lstm
02 dataset_creation_flat  ──► frame_features.csv ──► 04 random_forest_training
                                                  └─► 05 dense_nn_training
06 dataset_creation_face_crops ──► face_crops_index.csv ──► 07 cnn_training
                                                         └─► 09 dataset_creation_cnn_lstm ──► (10, not built)
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

**The CNN's first run (`07_cnn_training.ipynb`) produced an 84.75% test accuracy that is not
trustworthy, and the notebook has since been fixed rather than the number being reported as a
result:**

- `face_crops_index.csv` (from `06`) only covers **6 subjects** — the original Argus recordings,
  not yet the extracted UTA-RLDD ones (`subject_07+`). An 80/20 `GroupShuffleSplit(random_state=42)`
  over 6 subjects put 2 subjects in the test fold, and those 2 happened to have **zero `Alert`
  crops between them**.
- Against that degenerate test set, the model scored 84.75% accuracy purely by defaulting to
  `Low Vigilant` (89% of the test set): 0.00 recall on `Drowsy` (93 test examples, all missed) and
  `Alert` had no test examples to even score against. Training accuracy also hit >99% within 2
  epochs on a 28K-param model trained over only 4 subjects — a sign the model was keying on
  subject identity (skin tone/lighting/background) rather than drowsiness cues, not evidence of a
  genuinely easy task. Epoch-to-epoch validation accuracy swung between 0.11 and 0.89 for the same
  reason: with so few held-out subjects, each epoch's decision boundary either happens to favor
  those 2 subjects' appearance or it doesn't.
- **Fixed in the notebook** (not yet rerun): the split now uses `StratifiedGroupKFold`, restricted
  to only choose **test-fold subjects from among subjects that have all 3 classes** — an
  incomplete subject (missing a class entirely) still contributes its rows to training, it's just
  never eligible to be picked as a held-out test subject, since a test fold drawn from it could be
  missing a class no matter how the split algorithm balances things. This is a hard, provable
  guarantee (test always has every class), not just a lower-probability version of the original
  bug, and it raises an error if it's somehow still violated. Training now selects/checkpoints on
  macro-F1 instead of raw accuracy (which is what let the majority-class collapse look good in the
  first place); augmentation was strengthened (rotation/zoom/contrast on top of the existing
  flip/brightness) to make the subject-identity shortcut harder to exploit. Both `06` and `07` were
  also refactored to cut redundant cells (merged setup cells, consolidated the "Index CSV Summary"
  and "Dataset Sanity Check" sections into one, and the "Recovering an Index" utility in `06`
  dropped its legacy `_f{frame_idx}`-filename branch — the pipeline has exclusively written
  `_s{sample_idx}` filenames for a while, so that branch was dead weight for any future recovery).
  None of this substitutes for more subjects — see the next point.
- **The real fix is more subjects.** `raw_videos/` on Drive already has folders for `subject_07`
  onward (UTA-RLDD extraction has been run), but `06`'s own per-subject completeness check (added
  in response to this) shows only 9 of them actually have crops indexed so far, and 4 of those 9
  are missing a class — extraction needs to be resumed (rerun `06` in Colab; it's resume-aware and
  will only process what's missing) until its completeness check reports everything covered, not
  assumed to already be complete just because the folders exist.

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
   already builds the dataset index for a `TimeDistributed(CNN) → LSTM` hybrid: the same 1-6s
   windowed-sequence idea that makes the geometric LSTM work, applied to face-crop *images*
   instead of feature vectors. This is the most complete answer to the diagnosed problem, since it
   removes both limitations (single-instant *and* hand-engineered-summary) at once, not just one.

**Calibrated expectation, not a promise:** this is also, by a wide margin, the most data-hungry
architecture in this project — a deep model over raw pixels (or pixel *sequences*), trained on a
subject pool that's still only 6 for the CNN path (see "What we found" above) even though ~24 are
available once `06` is rerun against the extracted UTA-RLDD subjects. It's the best-reasoned next
bet given the evidence above, not a guaranteed win; treat it as a real experiment. `07`'s only run
so far was on a degenerate split and isn't a real read on this approach yet; `09` hasn't been run
at all, and the CNN+LSTM training notebook (`10`) doesn't exist yet — this is still mostly the
planned direction, not a result.

**This "most probable next backbone" reasoning has since become the current focus, not just a
recommendation**: `src/cv-argus` now actually deploys `07`'s single-frame CNN by default (see
"What we found" above and `src/cv-argus/CLAUDE.md`'s "Current status"). That's a statement about
what's *running*, though, not about what's *validated* — every caveat in this section and in
"What we found" (the degenerate 6-subject split, the incomplete extraction, the untrustworthy
84.75% figure) still applies exactly as written; being deployed didn't retroactively fix any of
it. Getting `06` rerun to full completeness and `07` re-evaluated on a trustworthy split remains
the actual next step, now with more urgency since it's no longer just a baseline being compared
against, but the model this project's edge device runs.

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
  `06`/`07` have been run, but `07`'s only result so far came from a degenerate 6-subject split
  (see "What we found") — don't report its 84.75% accuracy as a real number, and don't report
  `09`/`10` results as if they exist at all.
- When asked to improve accuracy on the flat dataset (RandomForest/Dense NN), don't propose
  hyperparameter tuning or generic regularization as a first move — re-read "What we found"
  above first. The ceiling is diagnosed as a single-frame information limit (Spearman |r| ≤
  0.26), not an undertuned model, and rolling/temporal feature enrichment was already tried and
  made things *worse*. A new proposal should explain why it addresses the single-instant-in-time
  root cause specifically, not just retry a variant of what's already ruled out.
- When asked what to build next, default to the CNN direction ("Why CNN is the most probable
  next backbone") rather than proposing a fresh architecture search — it's the reasoned next
  step given the evidence here, and notebooks 06/07/09 already exist in support of it (09 as a
  dataset-index step only; its training notebook `10_cnn_lstm_training.ipynb` doesn't exist yet
  and would need to be created).
- Don't edit `GeometricRatioFeatureLayer` in one of its four copies without checking the other
  three (`01_dataset_creation_lstm.ipynb`, `02_dataset_creation_flat.ipynb`,
  `08_deployment_export_lstm.ipynb`, `src/cv-argus/src/model/layers.py`) — there's no automated
  sync check, per the root `CLAUDE.md`.
- State findings the way this file does: measured numbers with their source, not aspirational or
  rounded-up claims — this project's own titulación-report standard (see the root `CLAUDE.md`'s
  "Model results and current status") applies to how you describe results here too.
