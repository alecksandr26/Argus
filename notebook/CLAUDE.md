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
| 06 | `dataset_creation_face_crops` | raw videos | `face_crops_index.csv` + `.jpg`s | ⬜ not run yet |
| 07 | `cnn_training` | `face_crops_index.csv` | CNN `.keras` | ⬜ not run yet |
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

**The LSTM (windowed geometric features) is the only model with a working, deployed pipeline**,
and remains the intended production model — see `03_model_training_lstm.ipynb`'s Design Decision
cell for the full architectural reasoning (recurrence over a buffered window captures
duration/velocity of eye closure, which nothing single-frame can see).

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
architecture in this project — a deep model over raw pixels (or pixel *sequences*), trained on
~24 subjects. It's the best-reasoned next bet given the evidence above, not a guaranteed win;
treat it as a real experiment. Neither `06`/`07` nor `09` have been run yet, and the CNN+LSTM
training notebook (`10`) doesn't exist yet — this is the planned direction, not a result.

## Recent implementation decisions in `06`/`07` (not yet run)

Before either notebook's first real run, three changes landed to address near-duplicate crops
and add training-time augmentation — worth knowing so you don't re-propose them as open
problems:

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
- **`07` now augments with Albumentations, not just plain `tf.image` ops.** The `training=True`
  branch of `make_dataset()` still flips left/right via `tf.image`, but brightness jitter is now
  a mild Albumentations `Compose` (`RandomBrightnessContrast`, `RandomGamma`,
  `HueSaturationValue`) applied online per epoch via `tf.numpy_function`, rather than baked into
  extra stored files in `06`.
- **MediaPipe's Face Detector (`BlazeFace`) was evaluated against a YOLO-based face detector and
  kept** (this comparison was originally written up as a "Design Decision" markdown cell in `06`
  itself; it was removed from the notebook and lives only here now). Short version: YOLO's
  accuracy edge is on scenarios (cluttered/occluded/wide-angle scenes) this project's
  single-frontal-driver-face setup doesn't have, while it would add real cost here — a heavier
  edge footprint, a second detection framework to maintain alongside the MediaPipe stack the LSTM
  path still needs, and Ultralytics YOLOv8's AGPL-3.0 licensing, a real concern for a project
  meant to become a commercial product.

## One more thing worth knowing before touching `08` or `cv-argus/`

`03_model_training_lstm.ipynb`'s `MAX_TIMESTEPS` changed from 60 to 120 partway through this
project's history (see `CLAUDE.md`). `src/cv-argus/src/model/lstm_model.py` still has
`max_timesteps: int = 60` as its Python-level default. This likely isn't broken for a model
*loaded* from a `.keras` file (Keras restores `max_timesteps` from the saved config, not the
class default), but it's stale as documentation and worth checking before trusting it blindly if
`08_deployment_export_lstm.ipynb` is re-run and re-exported. Not fixed here — flagged for
whoever picks that up next.

## Working in this directory

- Check the Pipeline map's Status column before assuming a notebook has been run — "⬜ not run
  yet" and "❌ does not exist yet" are load-bearing distinctions, not filler; don't report
  06/07/09/10 results as if they exist.
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
