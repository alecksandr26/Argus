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
pose-validity flag) with a `tf.keras.layers.Layer` (copied verbatim into notebooks 01/02/08/09;
the reference copy is `tests/reference_geometric_layer.py`, moved from cv-argus when cv-argus
dropped the CNN+LSTM pipeline on 2026-10-18). That layer is *only arithmetic* on values MediaPipe already
returns (landmark coords + the facial transformation matrix), and dataset creation only ever
**calls** it — never serialises/deserialises it. So `argus_dataset/geometry.py` reimplements
the same math in NumPy. This keeps ~0.5 GB of TensorFlow out of every spawned worker (real on
a RAM-constrained box) and lets the whole module depend on just
`mediapipe + opencv + numpy + pandas + tqdm`.

**Fidelity guard:** `tests/test_geometry_equiv.py` runs random landmarks / rotation matrices
through both `geometry.py` and the real `tf.keras` layer and asserts agreement to `atol=1e-4`
over 200 cases. It's the only place TensorFlow is imported (dev-only, `pip install -e .[dev]`,
skipped otherwise). If you touch `geometry.py`, this test is the contract — and **don't
"reconcile" the two by editing the reference layer**: it must stay the notebooks' class, which
trained `.keras` models (the archived CNN+LSTM, branch `archive/cnn-lstm-fused`) deserialize.

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

### 3. `cnn_lstm_windows`: minority-class overlap tiling

src/notebook/09 tiles **non-overlapping** windows for both classes (`stride == win_size`). This
pipeline keeps that for `level_1` (Not Drowsy) but tiles `level_2` (Drowsy) with overlap —
`config.CNNLSTM_MINORITY_WINDOW_OVERLAP` (default `0.5` → half stride). The raw clip pool is
~2:1 Not Drowsy : Drowsy and that ratio otherwise carries straight into the window index;
overlap-tiling only the minority class lifts its window count toward parity **without new
video**. `cnn_lstm.py`'s driver passes the per-clip overlap; `windowing.cnn_lstm_windows_for_clip`
takes it as `window_overlap` and defaults to `0.0` (so the pure function still reproduces 09's
behaviour when called directly, e.g. in tests). Set `CNNLSTM_MINORITY_WINDOW_OVERLAP = 0.0` to
disable. Subject-grouped splitting in the training notebook stays leak-safe — the split is by
*subject*, so all of a subject's windows (overlapping or not) land on the same side of every
boundary. **To regenerate after changing the overlap, just re-run
`scripts/build_cnn_lstm_windows.py`** — its step 2 (windowing) is pure and always re-runs over
the full geometry cache, rewriting the index. **No `--reset`** (that discards the ~44k-crop
geometry cache and forces the slow step-1 re-extraction). `cnn_lstm.run` uses its own
completed-log resume, not `RunCheckpoint`/`config_hash`, so the `_HASH_RELEVANT` entries for
`CNNLSTM_MINORITY_*` are there for completeness, not an active guard on this build. src/notebook/09
mirrors this (`MINORITY_WINDOW_OVERLAP`, per-class tiling spot-check) — keep the two in sync.

## Module map (`argus_dataset/`)

| module | responsibility |
|---|---|
| `bootstrap.py` | Thread pinning (`OMP_NUM_THREADS=1`, `CUDA_VISIBLE_DEVICES=-1`, …). **Import first**, before numpy/pandas/cv2/mediapipe — every script's first line is `import argus_dataset.bootstrap`. |
| `config.py` | **Every notebook-matching constant, in one place**, each annotated with its notebook + cell. `SAMPLING_FPS`, window configs, `MAX_TIMESTEPS`, the 51 `BLENDSHAPE_NAMES`, the 10 `GEO_FEATURE_NAMES`, CSV-column builders. `NUM_FEATURES` is **derived** (`7 + len(BLENDSHAPE_NAMES)` = 58), never hardcoded. `config_hash(artifact)` — the resume guard. |
| `paths.py` | `$ARGUS_DATASET_ROOT` resolution (default: this dir). `raw_dir()`, `processed_dir()`, `models_dir()`, `progress_dir()`, `cache_dir()`, canonical output-file paths. Warns if the root is on `/mnt/` (NTFS kills small-file throughput). |
| `geometry.py` | NumPy `GeometricRatioFeatureLayer` (see above). `compute_geometric_features` (batch-capable) + `frame_feature_row` (the 58-wide per-frame vector). |
| `pipelines.py` | MediaPipe wrappers, ports of the notebook classes: `FaceLandmarkerFeatureExtractor` (VIDEO mode, 01 c85 / 02 c73), `FaceCropExtractor` (BlazeFace, 06 c9), `extract_geo_for_crop` (IMAGE mode, 09 c13). `cv2`/`mediapipe` imported lazily so the parent process can ship these objects to workers without loading native libs early. Each instance holds only picklable primitives — never a live MediaPipe handle. |
| `windowing.py` | Pure functions, no I/O. `lstm_windows_for_clip` (01 c94 — slide, drop windows with an invalid-pose frame, zero-**pre**-pad to 30, flatten timestep-outer). `contiguous_runs` + `cnn_lstm_windows_for_clip` (09 c14 — per-contiguous-`sample_idx`-run tiling, `geometric_feature_seq` string format; `window_overlap` arg — `0.0`/non-overlapping default, driver passes `CNNLSTM_MINORITY_WINDOW_OVERLAP` for `level_2`, see departure 3). `enrich_frame_features` (02 c106-114 — `EAR_mean` + causal rolling mean/std + `_delta1`). |
| `workers.py` | The `spawn` pool, per-worker thread pinning + SIGINT-ignore, the atomic `Committer`, the three per-clip task functions (`lstm_task` / `flat_task` / `crops_task`), and `run_video_build` — the resumable driver shared by builds 01/02/06. |
| `checkpoint.py` | `RunCheckpoint`: `<artifact>.completed.jsonl` (authority for "what's done"), `<artifact>.json` (progress summary), `reconcile` (drop rows for keys missing from the log — interrupted commit), `prune_missing` (drop rows/log keys whose raw `.mp4` is gone — the inverse selector, used by `update.py`), `check_config_or_die` (the `config_hash` guard), `reset`. |
| `cnn_lstm.py` | Build 09's own driver (not a "video build"): parallel per-crop geometry → `.cache/geo_per_crop.parquet` (resumable), then windowing. |
| `collect.py` | Clip collection into the raw tree — subject/clip naming (`next_clip_number` = `max+1`, the no-overwrite guarantee), `record_webcam` (headless-capable), `import_file` (copy or re-encode), `append_collection_log` (write-as-you-go provenance), `quick_face_coverage` (optional BlazeFace sanity check). Driven by `scripts/collect_clips.py`. |
| `frames.py` | Stills for hand labelling (parts detector): one full frame every `interval_sec` from a webcam (`capture_stream`, wall time; reads continuously so the camera buffer never goes stale) or a video (`extract_from_video`, video time) into `raw/webcam_frames/subject_NN/<session>/` + a write-as-you-go `frames.csv`. `guided_plan` prompts through `DEFAULT_PHASES` (straight / eyes_closed / head_down / head_down_eyes_closed / yawn) with un-saved transitions; the tag in each filename is a labelling hint, not a label. Saved frames are never drawn on (overlays go on the preview copy). `next_subject_id` spans `raw_videos/` and `webcam_frames/` (one namespace). No build reads the stills directly: they come back either through `own_captures.py` (below, no labelling) or a hand-labelled Roboflow project (README 7c; that path still groups per still, not per subject). Driven by `scripts/capture_frames.py`. |
| `own_captures.py` | Guided stills → the `own-captures` head-pose source, with no Roboflow: a Roboflow-style export under `raw/roboflow/own-captures/v1/` plus a manifest entry, so `detection_dataset` needs no special case. `plan_still` (pure): a still tagged head-down (`OWN_HEAD_DOWN_TAGS`) gets a human `head_down` box (the BlazeFace face box) if pitch ≥ `OWN_HEAD_DOWN_MIN_PITCH_DEG` (15°) or the landmarks are lost, and is dropped below that. Every other tag gets an empty label, so the landmarks decide. The floor is under the 20° pitch cut because low cameras flatten a lowered head; it isn't lower because human `Head_forward` reaches 19.6°. `export` prints `pitch_by_tag` for recalibrating it per camera. Files are named `<subject>_jpg.rf.<hash>.jpg`, so `original_stem` groups each subject. Rebuilt wholesale each run. Driven by `scripts/add_own_captures.py`; smoke-tested 2026-10-23 with real MediaPipe on 30 headpose-w4zuc images posing as a session, then through `build_detection_dataset.py` (15 human `head_down` boxes, 0 groups crossing splits). Not yet run on real captures. |
| `update.py` | Incremental-update orchestration: `status` (per-artifact new/orphan diff of the raw tree vs. each `completed.jsonl`), `prune` (drop orphan rows + JPEGs), `run` (the `run_all.sh` build chain as one call). Driven by `scripts/update_dataset.py`. |
| `assets.py` | Idempotent download of `face_landmarker.task` + `blaze_face_short_range.tflite`. |
| `external.py` | **Download-only** pull + catalogue of the external datasets for the experimental "parts" architecture (eye/mouth crops → tiny CNNs): MRL Eye (zip → `raw/mrl_eye/`), the `config.ROBOFLOW_PROJECTS` YOLO exports (→ `raw/roboflow/<project>/v<N>/`), and a catalogue-only UTA-RLDD entry. Writes `raw/datasets_manifest.json` atomically. Skip-if-unchanged is checksum-based: zip sha256 for MRL, a whole-tree fingerprint for Roboflow. Its config constants are deliberately **not** in `_HASH_RELEVANT`: `config_hash` hashes every key, so adding them would invalidate every finished 01/02/06/09 run. Driven by `fetch_mrl_eye.py` / `fetch_roboflow_parts.py` / `datasets_status.py`. |
| `ir.py` | IR ("NoIR") simulation for RGB training crops, matching the cabin's Pi NoIR OV5647 + IR LEDs. Red-dominant channel mix (`IR_CHANNEL_WEIGHTS_BGR`), then histogram matching toward an MRL real-IR reference CDF, blended at `IR_HIST_MATCH_STRENGTH`. **Training sources only**: never applied to MRL (already IR) or at inference (real IR frames just go to gray). Random IR effects (glints, LED falloff, noise) are training-time augmentation and don't belong here. |
| `parts_dataset.py` | Merges MRL + the Roboflow exports into one 3-class dataset (`open_eye` / `closed_eye` / `yawn`, via `ROBOFLOW_CLASS_MAP`) under `processed/parts_dataset/` (`rgb/`, `ir/`, `parts_dataset.csv`, `class_map.csv`), plus `export_zip` → `processed/parts_dataset.zip`. **Leakage guard:** rows are grouped by Roboflow original stem (`<orig>_jpg.rf.<hash>` augmented copies) and unioned across exact 64-bit dHash matches; that also chains consecutive video frames, e.g. aunvg's NTHU-style `001_glasses_…` clips form one group. MRL is grouped by subject. `assign_splits` cuts **by hash rank** per source, because thresholding the hash with only 37 MRL subjects once left test empty. Unmapped source classes are a hard error. Rebuilt wholesale on each run (~1 min); crop files are reused. Driven by `build_parts_dataset.py`. |
| `verify.py` | Per-artifact schema/label/relationship checks; `--compare` against a Colab CSV. |
| `cli.py` | Shared argparse plumbing for `scripts/`. |

`scripts/` are thin argparse wrappers over the package — the four `build_*` and
`fetch_models` / `verify_artifacts` / `publish_to_drive` ones are `≤40 lines`;
`collect_clips.py` and `update_dataset.py` are larger only because they carry more
user-facing CLI surface, still with no logic of their own (it lives in `collect.py` /
`update.py`). `run_all.sh` chains the build steps, forwarding SIGINT to the active child.
`README.md` is the end-to-end runbook (extraction → build → verify → publish → incremental
updates).

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

## Raw-video sources, collection + incremental updates

Raw video reaches `raw/raw_videos/` two ways. The bulk of the current dataset is **UTA-RLDD**
sub-clips cut by `src/cv-argus/scripts/extract_uta_rldd_clips.py` (run against downloaded Kaggle
fold-zips; see its docstring). That script emits UTA-RLDD's native **3-class** `level_<1-3>`
scheme — it must be collapsed to this module's binary `level_1`/`level_2` before the builds see
it (`config.map_level` refuses `level_3` with a pointed error). The second source is new
recordings via `collect_clips.py` (below), which are binary from the start.

`scripts/collect_clips.py` (→ `collect.py`) is the front door for new raw video: it records
from a webcam or imports existing files into `raw/raw_videos/subject_NN/level_<1-2>_clip_NN.mp4`.
New real drivers continue the existing `subject_NN` numbering (`next_subject_id` = highest + 1);
there is deliberately **no separate prefix** — the leak-safe subject split downstream keys on the
folder name, so one namespace is simplest. `next_clip_number` returns `max(existing) + 1` and
`clip_path` refuses a path that exists, so collection cannot overwrite a clip. Each clip is
logged to `raw/raw_videos/collection_log.csv` immediately (write-as-you-go, never batched).

**The four builds are already incremental — this is not something to reimplement.** Each keeps
`<artifact>.completed.jsonl`; `workers.run_video_build` discovers all clips, subtracts the
completed keys, processes only the remainder, and *appends*. Adding clips + re-running a
`build_*.py` (or `run_all.sh`, or `update_dataset.py --run`) is the supported update path.
`--reset` is only for a `config.py` change (the `config_hash` guard, not new data).

`scripts/update_dataset.py` (→ `update.py`) adds the cross-artifact view: `status()` classifies
every raw clip as done / **new** (not yet processed) / **orphan** (a row or completed-log key
whose raw `.mp4` was deleted or renamed). Orphan handling is opt-in: `status` only reports,
`--prune` calls `RunCheckpoint.prune_missing` (rewrites `completed.jsonl` and the CSV, keeping
only keys whose file still exists) and deletes the orphaned `face_crops/*.jpg`. `cnn_lstm_windows`
has no `RunCheckpoint` (its geometry cache is keyed by crop path, its window CSV always
rebuilt), so after a prune re-run `build_cnn_lstm_windows.py` to regenerate the index.

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

## `detection_dataset.py`: the single dataset for the detector notebooks

The single YOLO-format dataset for notebooks 12, 13 and 14 is `processed/parts_det/` and
`parts_det.zip`. Images are face crops with 5 classes: `open_eye`, `closed_eye`, `yawn`,
`head_straight`, `head_down`. It's built in two passes:

- **MediaPipe pass.** BlazeFace runs on the full image, then FaceLandmarker IMAGE runs on the
  square face crop, giving pose plus per-eye contour box and EAR. The pass is chunk-cached under
  `.cache/det_mp/` (keyed by a hash of the chunk's paths) and resumable.
- **Build.** Cheap; always rebuilt.

The rules below each came from looking at real data:

- **The face crop is square:** `max(w, h) × 1.5`, black-padded. cv-argus's crop is clipped and
  not squared, so a future edge pipeline for these detectors must reproduce `square_face_box` +
  `crop_square`.
- **Pitch sign: positive means head down.** This was checked on contact sheets: yawning heads
  thrown back read about −30°. `head_down` was genuinely rare in the original sources (about 190
  boxes); the head-pose sources below are what lifted it. Head pose for the pipeline should still
  come from landmarks.
- **Head thresholds (v4, 2026-10-19):** straight = |pitch| ≤ 18° and |yaw| ≤ 22°, down = pitch ≥
  20°, otherwise no head box. Calibrated against headpose-w4zuc's human labels (pitch/yaw from
  these same landmarks):

  | label | pitch median | pitch 5–95% | |yaw| median |
  |---|---|---|---|
  | `Head_forward` | 7.6° | −6.9° … 19.6° | 5.9° |
  | `Head_down` | 34.9° | 25.2° … 42.0° | 17.2° |
  | `Head_left` / `Head_right` | — | — | 31–32° |

  A 20° down cut keeps 0.99 of down and 0.96 of forward. The old straight limit of 10° left 35% of
  human-forward heads (2,140 faces in the 10–20° band) with no head box. |yaw| 22° keeps 0.997 of
  forward heads and excludes 0.82 of turned ones (25° only excluded 0.73).
- **Human head-pose labels beat pitch** (added 2026-10-19). `ROBOFLOW_HEAD_CLASS_MAP` maps a
  source's head labels to `head_straight` / `head_down` / `None` ("neither": no head box). Every
  class of a head-pose project must be mapped, as a head label (`ROBOFLOW_HEAD_CLASS_MAP`) or a
  part (`ROBOFLOW_CLASS_MAP`, `None` = ignore it, so the landmarks decide that image's head):
  `check_head_maps` hard-errors with a name-based suggestion, and `--label-sheet` draws the raw
  labels so they can be checked first. The head box stays the **BlazeFace face box** (the same
  scope as every pitch-labelled head); only when BlazeFace misses (`head_source = human_box`) is
  the human head box used, with the crop built around it (margin `DET_HEAD_BOX_MARGIN_FRAC`, or
  per project `DET_HEAD_BOX_MARGIN_BY_PROJECT`: xsriz's boxes are face-sized). A human head box
  smaller than half the face (`DET_HUMAN_HEAD_MIN_REL`) is ignored: facial-movements has some
  `head_down` boxes drawn on an eye. `ROBOFLOW_HEADPOSE_PROJECTS`: `parts_dataset` skips them,
  their unlabelled eyes and mouths are pseudo-labelled, and `DET_HEADPOSE_ALLOW_INCOMPLETE_EYES`
  keeps head-only images; on a `head_down` head it also keeps an image whose unlabelled eye is
  unclear (downcast lids read between open and closed), leaving that eye unboxed. The trade-off:
  rejecting them would drop exactly the hard head-down cases, and keeping them means a visible but
  unlabelled eye is learned as background. `lm_head_class` records what pitch said, so
  `summarize()`'s `head_human_vs_landmarks` is the calibration check on `HEAD_*_DEG`.
  - **Sources:**
    - `vidyashree/headpose-w4zuc` (v3): head boxes in 5 positions + per-eye boxes, every box a
      4-corner polygon (`_read_yolo_labels` reads 4-corner polygons as their bounding box; longer
      polygons, jhmld's segmentations, are still skipped). `Eye_forward`/`left`/`right` (gaze)
      map to `open_eye`, `Eye_close` to `closed_eye`; many `Eye_close` boxes are downcast lids on
      lowered heads, a judgement call.
    - `karthik-madhvan/drowsiness-detection-xsriz` (v1, Public Domain): a truck cabin seen from a
      low dashboard camera, the closest match to the Pi's view. One face box per image carrying a
      driver state (`head_down`, `focused`, `head_up`, `seeing_left/right`, `eyes_closed`,
      `eyes_closed_head_left/right`, `yarning`). No eye boxes: eyes come from landmarks; mouths
      from its `yarning` state (`ROBOFLOW_MOUTH_LABELS`).
    - `mdulo-v/facial-movements-detection` (v1, CC BY 4.0): office/car webcams; whole-head boxes
      (`head_front/down/left/right/up`), per-eye boxes, `open_mouth`/`closed_mouth` (mouth state
      via `ROBOFLOW_MOUTH_LABELS`), pupil and seatbelt boxes (ignored).
    - **Added 2026-10-26**, chosen from per-class contact sheets of 9 Universe candidates (rejected:
      Deloitte's modified distracted driver set, whose 218 "d2 - Nodding Off" photos are the same
      as noddingoff's but with salt-and-pepper noise; TestDemo "sleeping", mostly side profiles,
      rotated images and eye-only boxes; posture-detection, classrooms with tiny faces;
      sleep-driving, tight crops of level closed-eye faces; drowsy-utudu, 155 images):
      - `testproject-8jvqn/kantuk-edo70` (v1, CC BY 4.0): car cabins, face boxes `head_dropped` /
        `head_straight`. Its eye box spans **both eyes**, so eyes come from landmarks
        (`ROBOFLOW_CLASS_MAP` → None); `yawn` / `no_yawn` become mouth states. `head_dropped`
        also holds some level yawning or laughing faces.
      - `head-movement/headmovement` (v12, Public Domain): office webcam face boxes in Indonesian:
        `bawah` down, `depan` front, `kiri` / `kanan` left / right, `-` looking up into a low
        camera, `r` a rolled head. A few `bawah` faces look nearly level.
      - `universe-adweg/noddingoff-z8zad` (v1, CC BY 4.0): side-view drivers nodding off, one
        whole-body `Distraction` box per image.
    - **`ROBOFLOW_HEAD_PITCH_CHECKED_PROJECTS`** (the three above): their head labels were seen to
      be noisy, so `human_head_agrees` checks them. Human `head_down` needs pitch ≥
      `DET_HUMAN_HEAD_DOWN_MIN_PITCH_DEG` (15°, own_captures' floor, for the same reasons); human
      `head_straight` needs the landmarks to say straight. A disagreement **rejects** the image
      (`head_label_disagrees`) rather than falling back to pitch: in a side view a real nod can
      read as low pitch, so the pitch guess could be the wrong label. No landmarks → the label stands.
      `DET_HEAD_CHECK_MAX_YAW_DEG_BY_PROJECT` (noddingoff only, 30°) skips the head_down check on
      faces turned beyond it: the first build rejected 85 noddingoff images that the contact sheet
      showed were all real nods in profile (yaw 37–64°, pitch ≈ 0). Not for kantuk, where a turned,
      level yawning face (p −20°, y −32°) would get through.
    - **`ROBOFLOW_IMAGE_LEVEL_HEAD_PROJECTS`** (noddingoff): the box isn't a head box, so the label
      goes on the BlazeFace face, skipping `DET_HUMAN_HEAD_MIN_REL` and the inside-the-crop check,
      and a missed face is `no_face` (no crop around a body box).
    - **`ROBOFLOW_BOX_BRIGHTNESS_AUGMENTED_PROJECTS`** (headmovement): Roboflow's per-box
      brightness augmentation (`bbbrightness`, in every headmovement version from v6) leaves a
      bright or dark rectangle on the face. `box_brightness_step` measures the straight step along
      the box edges; above `DET_BOX_BRIGHTNESS_MAX_STEP` (5 grey levels) the image is rejected
      (`box_brightness_artifact`). Measured on v12: unaugmented valid median 1 (p95 4), augmented
      train median 37. Checked in the build loop (it needs pixels), not in `plan_roboflow_image`.
      Every other source's augmentation is whole-image (checked through the API, 2026-10-26).
  - **v5 build (2026-10-26)**, with the three sources above. **MRL Eye was not in it**: its server
    (mrl.cs.vsb.cz) timed out all day, so this build has no MRL close-ups and its IR copies are the
    red-channel mix without histogram matching (`ir.to_ir_like` with no reference). Rebuild once
    MRL is fetched; the Roboflow numbers below won't change. That rebuild takes half as much MRL
    as v4 (`DET_MRL_MAX_PER_SUBJECT_CLASS` 300 → 150, the user's call on 2026-10-26): MRL's
    whole-image eye boxes are the likely reason the detector boxed whole lowered heads as one
    `closed_eye`.

    | | v4 | v5 |
    |---|---|---|
    | `head_down` boxes (train / valid / test) | 2,069 (1,437 / 333 / 299) | 2,689 (1,862 / 447 / 380) |
    | distinct `head_down` groups (train / valid / test) | 996 (693 / 153 / 150) | 1,599 (1,104 / 265 / 230) |
    | `head_straight` boxes | 9,160 | 10,460 |

    New `head_down` by source (boxes / distinct groups): kantuk 268 / 266, noddingoff 203 / 192,
    headmovement 149 / 145. Rejected in the new sources: `head_label_disagrees` 1,347 (mostly
    kantuk: 554 `head_dropped` at median pitch 1.4°, on the sheet yawns, level faces and slight
    dips; 691 `head_straight`, mostly turned), `box_brightness_artifact` 2,436 (headmovement).
  - **`ROBOFLOW_LANDMARK_ONLY_PROJECTS`** (empty so far): sources whose labels are ignored
    entirely. Head from pitch/yaw, eyes and yawn from landmarks; no landmarks → `no_landmarks`.
    For face photos that add head poses but carry no usable part labels.
  - **v4 build (2026-10-19), against v3 (the zip notebook 12's first run trained on):**

    | | v3 | v4 |
    |---|---|---|
    | `head_down` boxes (train / valid / test) | 644 (455 / 92 / 97) | 2,069 (1,437 / 333 / 299) |
    | distinct `head_down` groups (train / valid / test) | 231 / 44 / 54 | 693 / 153 / 150 |
    | `head_straight` boxes | 5,268 | 9,160 |
    | face crops with no head box | 54% | 43% |
    | accepted images | 34,488 | 41,421 |

    `head_down` by source: xsriz 917, headpose-w4zuc 532, facial-movements 438, the older sources
    182. Its test AP now carries about ±0.06 instead of ±0.10. Human vs. landmark labels where
    both exist: human `head_down` was pitch-`head_down` 918 times, pitch-none 360, pitch-straight
    92 (xsriz's low camera makes a lowered head look more frontal). Remaining gaps are mostly real
    "neither" poses (turned, looking up), which correctly have no head box.
- **FaceLandmarker retries** (`DET_LM_RETRIES`, v4). On 1,945 real-face crops of v3 the standard
  crop gave no landmarks, mostly wide-open yawns and turned heads. Retrying wider crops (margin
  0.25 / 0.75 / 1.25, 192–320 px) at confidence 0.2 recovers about 40% of those (1,122 accepted
  crops in v4); `lm_attempt` records which crop worked and `to_std_crop` maps the landmarks back
  into the standard crop. Skipped when BlazeFace's box is ≥ 60% of the image side: that is almost
  always an eye close-up BlazeFace misread. The recovered heads are mostly straight/turned/up,
  not down. The MediaPipe cache is versioned (`DET_MP_CACHE_VERSION`, `.cache/det_mp_v<N>/`);
  bump it whenever `mp_one`'s output changes. The full pass takes about 2 minutes on 12 workers.
- **Stale files.** The build reuses images across runs, and splits are cut by rank over all
  groups, so adding a source moves some groups to another split. Until v4 nothing deleted the
  old copies: v3 had 54 images left in their old split folder (harmless for notebooks 12/14, which
  read `index.csv`, but Ultralytics in 13 reads folders). `build` now deletes every image and
  label it didn't write.
- **Unlabelled-object guard.** Detectors learn unlabelled objects as background, so:
  - images carrying ambiguous labels are rejected;
  - face images need ≥ 2 eye boxes;
  - eyes the annotators skipped are pseudo-labelled from landmarks (`DET_PSEUDO_*`, calibrated on
    4,071 human eyes), except on faces turned more than 40° (`DET_PSEUDO_EYE_MAX_YAW_DEG`), where
    the far eye is hidden and the mesh puts it on the nose or cheek;
  - yawns nobody boxed are pseudo-labelled from the `jawOpen` blendshape (v4, every source):
    above 0.7 a yawn box of 1.3 × 1.4 the outer-lip box. Sources without yawn labels also reject
    0.5–0.7 as unclear. Calibrated on 7,123 face crops with 2,355 human yawn boxes plus contact
    sheets: unboxed faces above 0.7 are real yawns the annotators skipped (mostly jhmld); 0.6–0.7
    is talking or surprise; below 0.5 is not a yawn. Some very wide yawns read ≈ 0 because the mesh
    closes the mouth, so a low `jawOpen` never removes a human yawn box. Human mouth labels
    (`ROBOFLOW_MOUTH_LABELS`) beat `jawOpen`: on facial-movements, 833 `jawOpen` "yawns" were on
    human `closed_mouth` images.

  An eye counts as already labelled if its landmark centre lies inside a human box; this catches
  wide both-eyes or glasses boxes.
- **Grouping:**
  - Roboflow original stems group augmented copies. In `DET_STEM_UNRELIABLE_PROJECTS` (jhmld, a
    merge of several sources with colliding names like `1.jpg`), stem-mates join only if their
    16×16 thumbnails correlate ≥ 0.75 (flip-invariant).
  - dHash joins exact re-uploads across projects only.

  Without these rules, one chained group once held 6.4k images and 1.9k of the 2.5k yawns.
- **Splits:** rank-based by group and per source (`parts_dataset.assign_splits`), with yawn
  groups split separately. The result is about 68/16/16 for yawns, and 0 groups cross splits.

## TODO (deferred): offline augmentation of the parts dataset

Not built yet; parked on 2026-10-17 to prioritise the training notebook. The idea is a
`scripts/augment_parts_dataset.py` that reads `processed/parts_dataset/` and writes a bigger,
seeded (reproducible) `parts_dataset_aug/` + `.zip`. Rules agreed so far:

- **Train split only.** Each copy keeps its source row's `group_id` and `split`, plus an
  `augmented_from` column; valid/test stay untouched.
- **Oversample `yawn`** (about 4% of crops). MRL is probably left alone: it's already 85k real-IR
  images.
- **Camera-realistic grayscale ops:** small affine (rotation/shift/scale), gamma/brightness,
  blur, sensor noise, low-res down/up-scaling, IR corneal glints (eyes), radial LED falloff, and a
  random blend between plain grayscale and `ir.to_ir_like` for RGB sources (covers daytime NoIR).
- Roboflow exports already contain ~3× augmented copies (kept, grouped by original stem).
- Open question: offline copies vs. on-the-fly augmentation in the training notebook. On-the-fly
  is usually better, so offline is mainly worth it for a fixed, shareable zip.

## What's verified vs. not

- **Verified:** the test suite (`pytest` → 115 pass, 1 skipped without `[dev]` on 2026-10-26;
  the skip is the geometry-equivalence check, which needs `tensorflow`); the NumPy
  geometry port vs. the real `tf.keras` layer to `atol=1e-4`; the spawn pool + checkpoint +
  resume + model-download flow end to end on synthetic video; the collector's no-overwrite
  naming, verbatim / re-encode import, provenance log, and `record_webcam`'s write path against
  a fake camera (`test_collect.py`); `frames.py`'s capture loop, guided plan and video
  extraction against a fake camera + clock (`test_frames.py`); the incremental
  new-vs-orphan diff and `prune_missing` (`test_update.py`).
- **Not verified on the dev box:** real MediaPipe inference — it needs system shared libs
  (`libgles2`/`libegl1`/…, see `README.md`) that weren't installable in the environment this
  was built in. So feature-*value* fidelity against a real Colab run is unconfirmed (expected —
  see "Same artifact" above). First real run should `verify_artifacts.py` and, if a Colab CSV
  is handy, `verify_artifacts.py --compare`. Real webcam capture (`collect_clips.py`,
  `capture_frames.py`) has still not run against a physical camera; the fake-camera tests
  caught that `record_webcam`'s `*.mp4.partial` temp file never opened (OpenCV picks the
  container from the extension), so every webcam clip it "recorded" was empty. It now writes
  `.<clip>.partial.mp4` (hidden, so `discover_clips`' glob can't pick up a crash leftover).
