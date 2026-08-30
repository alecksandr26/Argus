# CLAUDE.md — src/dataset

Guidance for working in `src/dataset/`, the **local reimplementation of Argus's
dataset-creation stage**. Read the root `CLAUDE.md` and `src/notebook/CLAUDE.md` first for the
ML-pipeline context. `README.md` next to this file is the practical "how do I run it" guide;
this file is the architecture and the *why*, plus the notebook-fidelity contract — read it
before changing anything here, not just running it.

## What this is and why it exists

The four Colab notebooks that turn raw drowsiness-labelled video into training data —
`src/notebook/01_dataset_creation_lstm`, `02_dataset_creation_flat`,
`06_dataset_creation_face_crops`, `09_dataset_creation_cnn_lstm` — are pure CPU work (OpenCV
decode + MediaPipe inference + cheap geometric-ratio math). They get nothing from Colab's GPU,
and Colab kept interrupting the multi-hour extraction runs. So they were reimplemented as
local, CPU-parallel, **pausable/resumable** scripts that run in WSL2 on the dev box.

**These scripts are the source of truth for dataset creation now.** The four notebooks are kept
as Colab-runnable reference but are no longer the thing to edit. The **training** notebooks
(`03`/`04`/`05`/`07`/`08`/`10`) are unaffected — they still run on Colab for the GPU, consuming
the CSV/JPEG artifacts this pipeline produces (uploaded via `scripts/publish_to_drive.py`).

### "Same artifact" means same schema, not same bytes

MediaPipe / XNNPACK inference is not reproducible across machines / thread counts / builds. A
local run and a Colab run will produce *statistically equivalent* feature distributions and
*identical* schemas, but low-order digits differ, and frames near
`min_face_detection_confidence = 0.5` can flip detected/not — shifting row counts and which
windows survive the pose-validity gate. `scripts/verify_artifacts.py` enforces the schema /
label / relationship contract the training notebooks rely on; it does **not** assert value
equality. The training notebooks retrain from scratch and tolerate this.

## Two deliberate departures from the notebooks

### 1. No TensorFlow — `geometry.py` is a NumPy port of `GeometricRatioFeatureLayer`

The notebooks compute the 7 geometric features (EAR×2, MAR, head-pose pitch/yaw/roll, a
pose-validity flag) with a `tf.keras.layers.Layer` (`src/cv-argus/src/model/layers.py`, copied
verbatim into notebooks 01/02/09). That layer is *only arithmetic* on values MediaPipe already
returns (landmark coords + the facial transformation matrix), and dataset creation only ever
**calls** it — never serialises/deserialises it. So `argus_dataset/geometry.py` reimplements
the same math in NumPy. This keeps ~0.5 GB of TensorFlow out of every spawned worker (real on
a RAM-constrained box) and lets the whole module depend on just
`mediapipe + opencv + numpy + pandas + tqdm`.

**Fidelity guard:** `tests/test_geometry_equiv.py` runs random landmarks / rotation matrices
through both `geometry.py` and the real `tf.keras` layer and asserts agreement to `atol=1e-4`
over 200 cases. It's the only place TensorFlow is imported (dev-only, `pip install -e .[dev]`,
skipped otherwise). If you touch `geometry.py`, this test is the contract — and note that
`layers.py` itself is loaded back out of trained `.keras` files by `cv_argus`'s `detector.py`,
so **don't "reconcile" the two by editing `layers.py`** — that's a deploy-side change.

This is *not* a 6th verbatim copy of the layer (the root `CLAUDE.md` tracks five); it's a
separate implementation kept honest by the equivalence test.

### 2. No relabel step — the raw tree is expected already binary

The Colab flow has `relabel_binary_raw_videos.ipynb` derive a `raw_videos_binary/` tree
(`{1,2}→1, 3→2`) from the 3-class `raw_videos/` source of truth. The local pipeline skips this:
it reads `raw/raw_videos/subject_NN/level_<1-2>_clip_NN.mp4` directly and expects the clips
**already** labelled `level_1` = Not Drowsy / `level_2` = Drowsy. `config.map_level` validates
`{1,2}` and gives a pointed error on `level_3`. `src/cv-argus/scripts/extract_uta_rldd_clips.py`
still emits 3-class `level_<1-3>` (UTA-RLDD's native scheme) — collapse those to binary at the
source before feeding this pipeline.

## Module map (`argus_dataset/`)

| module | responsibility |
|---|---|
| `bootstrap.py` | Thread pinning (`OMP_NUM_THREADS=1`, `CUDA_VISIBLE_DEVICES=-1`, …). **Import first**, before numpy/pandas/cv2/mediapipe — every script's first line is `import argus_dataset.bootstrap`. |
| `config.py` | **Every notebook-matching constant, in one place**, each annotated with its notebook + cell. `SAMPLING_FPS`, window configs, `MAX_TIMESTEPS`, the 51 `BLENDSHAPE_NAMES`, the 10 `GEO_FEATURE_NAMES`, CSV-column builders. `NUM_FEATURES` is **derived** (`7 + len(BLENDSHAPE_NAMES)` = 58), never hardcoded. `config_hash(artifact)` — the resume guard. |
| `paths.py` | `$ARGUS_DATASET_ROOT` resolution (default: this dir). `raw_dir()`, `processed_dir()`, `models_dir()`, `progress_dir()`, `cache_dir()`, canonical output-file paths. Warns if the root is on `/mnt/` (NTFS kills small-file throughput). |
| `geometry.py` | NumPy `GeometricRatioFeatureLayer` (see above). `compute_geometric_features` (batch-capable) + `frame_feature_row` (the 58-wide per-frame vector). |
| `pipelines.py` | MediaPipe wrappers, ports of the notebook classes: `FaceLandmarkerFeatureExtractor` (VIDEO mode, 01 c85 / 02 c73), `FaceCropExtractor` (BlazeFace, 06 c9), `extract_geo_for_crop` (IMAGE mode, 09 c13). `cv2`/`mediapipe` imported lazily so the parent process can ship these objects to workers without loading native libs early. Each instance holds only picklable primitives — never a live MediaPipe handle. |
| `windowing.py` | Pure functions, no I/O. `lstm_windows_for_clip` (01 c94 — slide, drop windows with an invalid-pose frame, zero-**pre**-pad to 30, flatten timestep-outer). `contiguous_runs` + `cnn_lstm_windows_for_clip` (09 c14 — non-overlapping tiling per contiguous `sample_idx` run, `geometric_feature_seq` string format). `enrich_frame_features` (02 c106-114 — `EAR_mean` + causal rolling mean/std + `_delta1`). |
| `workers.py` | The `spawn` pool, per-worker thread pinning + SIGINT-ignore, the atomic `Committer`, the three per-clip task functions (`lstm_task` / `flat_task` / `crops_task`), and `run_video_build` — the resumable driver shared by builds 01/02/06. |
| `checkpoint.py` | `RunCheckpoint`: `<artifact>.completed.jsonl` (authority for "what's done"), `<artifact>.json` (progress summary), `reconcile` (drop orphan rows), `check_config_or_die` (the `config_hash` guard), `reset`. |
| `cnn_lstm.py` | Build 09's own driver (not a "video build"): parallel per-crop geometry → `.cache/geo_per_crop.parquet` (resumable), then windowing. |
| `assets.py` | Idempotent download of `face_landmarker.task` + `blaze_face_short_range.tflite`. |
| `verify.py` | Per-artifact schema/label/relationship checks; `--compare` against a Colab CSV. |
| `cli.py` | Shared argparse plumbing for `scripts/`. |

`scripts/` are thin (`≤40-line`) argparse wrappers over the package. `run_all.sh` chains them,
forwarding SIGINT to the active child.

## Parallelism — the substantive change vs. the notebooks

- **`spawn`, never `fork`.** Forking after MediaPipe/OpenCV have loaded inherits their native
  thread pools (and any CUDA context) — the documented cause of
  `BrokenProcessPool: terminated abruptly` that notebook 01's comments describe. `workers.py`
  uses `multiprocessing.get_context("spawn")`; each worker is a clean interpreter.
- **`initializer=`** builds the per-worker pipeline once, from primitives. Never pass a live
  MediaPipe handle through `submit()` — it segfaults on unpickle (notebook 02 does exactly
  this and is the anti-pattern; notebook 09 is single-threaded — both are "fixed" here).
- **Per-worker thread pinning** (`bootstrap.configure_process_threads` + `cv2.setNumThreads(0)`)
  — the parallelism is across clips, so each clip's decode + inference must be single-threaded
  or N workers × N threads thrash the box.
- **Streaming CSV + bare-count return.** A worker buffers one clip's rows, commits them
  atomically, and returns only an `int`. `concurrent.futures` retains every `Future` (and its
  result) for the whole run — returning row lists would accumulate the entire corpus in memory
  (this is the exact RAM failure the move off Colab was partly about; the same fix is in
  notebook 01's cell 94).
- **`--workers` default** is RAM- and CPU-aware (`workers.default_workers`): ~24 at 48 GiB,
  self-limits to ~6–12 at 15 GiB, so the scripts stay correct even if the `.wslconfig` RAM
  bump is skipped.

## Pause / interrupt / resume — the design

Unit of work = one clip (01/02/06) or one crop (09 step 1).

- **Atomic per-clip commit.** `Committer.commit` appends the clip's rows to the artifact CSV
  and the `(subject, parent_video)` key to `<artifact>.completed.jsonl` **under one lock**,
  `fsync`ing both. A kill outside that block loses nothing; a kill inside its sub-millisecond
  window is repaired by `reconcile`.
- **`completed.jsonl` is the authority.** On resume, `reconcile` rewrites the CSV without any
  row whose key isn't in it (orphans from an interrupted commit), then the driver skips every
  key already present.
- **Signals.** The parent handles SIGINT **and** SIGTERM (shutdown): stop submitting, let
  in-flight clips finish, save progress, print the resume line, exit 0. Second SIGINT within
  3 s → hard exit. Workers `SIG_IGN` SIGINT so only the parent drives shutdown.
- **`config_hash` guard.** `check_config_or_die` refuses to resume onto an artifact built under
  a different `config.py` (constants that affect artifact contents). `--reset` rebuilds clean;
  `--force` overrides (mixes incompatible rows — don't).
- **`build_cnn_lstm_windows.py`** — step 1 (slow per-crop geometry) streams to a parquet cache
  with its own completed-log; step 2 (windowing) is fast and pure, always re-run.

## The notebook-fidelity contract

When you change feature extraction / windowing / sampling here:

1. Change the constant in `config.py` (not scattered through modules).
2. **Mirror it back into the matching notebook cell** — the notebook is still the Colab
   reference and the `src/notebook/CLAUDE.md` empirical record is written against its values.
3. If it changes what an artifact contains, add it to `config._HASH_RELEVANT` so a stale
   resume is caught.
4. Re-run the relevant `tests/` (windowing / enrichment / geometry) — they encode the
   notebook's behaviour (zero-**pre**-padding, the `ear_mar_valid` gate, causal rolling, the
   `geometric_feature_seq` string format, `%.8g` / `%.6f` float formatting).

## What's verified vs. not

- **Verified:** the test suite (`pytest` → 19 pass + geometry-equiv; `[dev]` → 20); the NumPy
  geometry port vs. the real `tf.keras` layer to `atol=1e-4`; the spawn pool + checkpoint +
  resume + model-download flow, end to end, on synthetic video.
- **Not verified on the dev box:** real MediaPipe inference — it needs system shared libs
  (`libgles2`/`libegl1`/…, see `README.md`) that weren't installable in the environment this
  was built in. So feature-*value* fidelity against a real Colab run is unconfirmed (expected —
  see "Same artifact" above). First real run should `verify_artifacts.py` and, if a Colab CSV
  is handy, `verify_artifacts.py --compare`.
