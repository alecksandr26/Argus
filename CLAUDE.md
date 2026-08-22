# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Argus is a Driver Monitoring System (DMS) for truck drivers in Mexico. Instead of full
automation (Level 5), it aims at "Human Augmentation": a technological safety barrier against
fatigue, drowsiness, and health emergencies — a preventive layer on top of a human driver, not
a replacement for one. This positioning (see `docs/argus-descripción-proyecto.pdf`) is
deliberate: full autonomy is judged infeasible in Mexico near-term due to cargo-theft/security
risk (a stopped autonomous truck is an easy target), poor road infrastructure/lane markings and
lack of low-latency 5G, and cost (~$450k USD for a Level-4 truck vs. ~$180k conventional).

This is also an academic titulación ("trabajo de grado") project for an Ingeniería en
Computación program, and its architecture is explicitly shaped to satisfy the program's
grading criteria (`docs/criterios/criteriosaprobacion_0.pdf`), which require covering three
modules: **Arquitectura y Programación** (justified language/data-structure/methodology
choices, system modeling), **Sistemas Inteligentes** (ML/CV with a justified mathematical
model), and **Sistemas Distribuidos** (a genuinely decentralized system — not just a UI
consuming a centralized service — with real-time sync between at least two devices and
justified communication protocols). Keep this in mind when proposing designs: a suggestion
that collapses the distributed edge/cloud split into a single monolith would satisfy the
product but not the degree requirement.

### Planned end-to-end system architecture

The full system (see `docs/designs/semantic-design.drawio.png` / the underlying `.drawio` XML
at `docs/designs/semantic-design`) has two halves. Only the ML piece (below) exists as code so
far; the rest is design work to be implemented.

**Truck cabin (edge, hard real-time / safety-critical):**
- A camera captures frames, read by the **Raspberry Pi 5 ("AI Orchestrator")**, which runs the
  MediaPipe FaceLandmarker + geometric-ratio-layer + sequence-model pipeline from the notebook
  and produces a drowsiness class (`Alert` / `Low Vigilant` / `Drowsy`). The sequence model is an **LSTM**; this had briefly
  been under evaluation against a plain feedforward deep neural network over the same windowed
  features, but a detailed review confirmed the LSTM is the right choice and settled it, not
  the DNN — a plain (non-`stateful`) `tf.keras.layers.LSTM` already performs genuine recurrence
  *within* each call (real `h_t`/`c_t` gating across the buffered window, confirmed against the
  actual `lstm_model.py` call chain), which is exactly the duration/velocity-of-eye-closure
  signal a Dense layer structurally cannot see — see `03_model_training_lstm.ipynb`'s "Design
  Decision" markdown cells for the full writeup. `05_dense_nn_training.ipynb` does now contain a
  real Dense feedforward network, but it isn't a rerun of that deferred comparison: it trains on
  the flat, single-frame dataset (`02_dataset_creation_flat.ipynb`), not on the LSTM's windowed
  sequences, so it's a non-recurrent baseline alongside RandomForest rather than a like-for-like
  challenger to the LSTM on the same task. If `docs/document/borrador-proyecto-modular-
  argus.md` still frames the windowed DNN-vs-LSTM question as open, treat this file as the more
  current status. A
  steering-wheel grip sensor feeds the same decision orchestration node.
  Outgoing alert/status events are written to a local **SQLite queue/buffer** — this is the
  edge-side alert queue only, separate from the cloud database below — so nothing is lost when
  connectivity drops (this satisfies the "Memoria Local (Buffer)" MVP requirement — buffered
  events are auto-resent once a connection is available).
- A separate **ESP32 ("Message Sender Orchestrator")** reads that buffer and owns everything
  actuation- and safety-critical: it drives the **alarm speaker**, the **CAN Bus/AEB actuator**
  (preventive autonomous braking), reads the **panic button** and **geolocation module**, polls
  the Pi's SQLite buffer over **Bluetooth** (the ESP32 initiates periodic pulls of unsent
  records — the Pi doesn't push), and is the device that talks to the backend over **HTTP**.
- Deliberate split: the Pi *decides* (heavy AI inference, containerizable, can be redeployed via
  OTA), the ESP32 *acts* (bare-metal/real-time, must not depend on a Linux/Docker boot cycle
  completing). Don't move CAN-bus/alarm/panic-button logic onto the Pi — keep that boundary.

**Cloud/server side (planned as Docker Compose):**
- A **backend API** (**FastAPI**) exposing REST resources for `users`, `trucks`, `drivers`,
  `alerts`, `routes`, and `routes/:id/status`, backed by **MongoDB** accessed through an
  ORM/ODM (e.g. Beanie or PyMongo with Pydantic models) rather than raw driver calls.
- An **OSRM** (`Project-OSRM/osrm-backend`) container computing routes from a prebaked
  OpenStreetMap extract, queried by the backend/frontend for route + ETA data.
- A **React** frontend using **react-leaflet** to render the OSRM route and live truck/alert
  status, with panels for login, fleet management, driver/user access, route tracking, alerts,
  travel management, and reports.
- Three actor roles: Root/Admin (manage users, trips, reports), Guardian (monitor trips/alerts),
  Truck Driver (receives alerts/status).

When implementing the edge side, containerize the Pi's AI-orchestrator service (reproducible
MediaPipe/TensorFlow/OpenCV versions, easy redeploys) but pass through specific devices
(`--device=/dev/video0`, the relevant `/dev/video*`/`/dev/dma_heap/*` nodes for the CSI camera
via libcamera, or whatever Bluetooth access the container needs for the Pi↔ESP32 link — e.g. a
`/dev/rfcommN` device node, or the host's BlueZ/D-Bus socket — now that Pi↔ESP32 is decided as
Bluetooth rather than serial or Wi-Fi/MQTT) rather than `--privileged`. The CSI-camera-through-
Docker path is the one detail worth prototyping early, since libcamera device passthrough is
fussier than a USB webcam.

## Repository state and structure

Only two parts of the planned architecture exist as code so far: the training notebook and
the `cv-argus` edge module (below). The backend, frontend, Docker Compose stack, and ESP32
firmware described in "Planned end-to-end system architecture" don't exist yet.

- `notebook/01_dataset_creation_lstm.ipynb` through `notebook/08_deployment_export_lstm.ipynb` —
  the ML pipeline, split into eight stage-scoped notebooks across four model families (LSTM,
  RandomForest, Dense NN, face-crop CNN) that share two dataset-creation notebooks. See "Notebook
  architecture" below for what each one does and how they hand off to each other. These are the
  files to read and edit for almost any task involving the model itself. `notebook/ArgusMLModel.ipynb`
  is the original monolithic notebook this pipeline was split from; it's kept for history but is
  no longer the one to edit. There is no build system, package manifest, linter, or test suite
  for any of them — they're designed to run in Google Colab (mounts Google Drive as their storage
  backend, and each depends on a prior notebook's Drive outputs — CSVs, then `.keras`/`.joblib`
  models — rather than shared in-memory state, so they can be run as separate Colab sessions), not
  as local scripts; validate changes by reasoning about the notebook cells or, if actually
  executing them, running them inside Colab (or a Jupyter environment with `mediapipe`,
  `opencv-python`, `tensorflow`, `scikit-learn`, `pandas`, `joblib` installed) rather than assuming
  a local CLI workflow exists.
- `src/cv-argus/` — the Raspberry Pi 5 edge module: loads the trained model from the notebook
  and runs live inference against camera frames. Docker-first (see its own `README.md` for
  why and how); read that file before working in this module, it has the architecture details
  and notebook-fidelity notes (exact model input/output shapes, why the model classes must be
  ported verbatim, etc.) that would otherwise need re-deriving from the notebook each session.
- `docs/argus-descripción-proyecto.pdf` — project description/proposal.
- `docs/criterios/` — academic thesis/grading-criteria documents (this is a school "trabajo de
  grado" project); `Formato_Proyecto_Modular V2.docx` is the report template being filled in.
- `docs/designs/semantic-design*` — draw.io system architecture diagram; source of truth for
  the planned end-to-end architecture summarized above (`semantic-design` is the raw XML,
  `semantic-design.drawio.png` a rendered export — re-render after editing the XML).
- `docs/references/` — background research papers on drowsiness/microsleep detection that
  inform feature and model choices.

## Notebook architecture

Each notebook is a linear pipeline; later cells depend on variables/objects defined by earlier
cells *within the same notebook* (Colab-style top-to-bottom state, not a package with imports).
Handoff *between* notebooks is exclusively via Drive artifacts (CSVs, then `.keras`/`.joblib`
models), never shared Python state — each notebook re-mounts Drive and redefines its own
constants/classes in a "Setup" section at the top rather than assuming a prior notebook's
session is still live.

There are four model families, each trained on one of two datasets built from the same raw
videos. Two dataset-creation notebooks feed all four model-training notebooks:

```
01_dataset_creation_lstm.ipynb ──► lstm_windows.csv   ──► 03_model_training_lstm.ipynb ──► 08_deployment_export_lstm.ipynb
02_dataset_creation_flat.ipynb ──► frame_features.csv ──► 04_random_forest_training.ipynb
                                                       └─► 05_dense_nn_training.ipynb
06_dataset_creation_face_crops.ipynb ──► face_crops_index.csv + face_crops/*.jpg ──► 07_cnn_training.ipynb
```

The LSTM is the only model family with a deployment-export notebook — it's still the model the
edge pipeline is designed around (see `03_model_training_lstm.ipynb`'s Design Decision cell).
RandomForest, the Dense NN, and the CNN exist as trained-and-evaluated baselines/comparisons, not
as deployment candidates with their own export path yet.

**`01_dataset_creation_lstm.ipynb`** — raw video → windowed, padded features for the LSTM:

1. **Labels & feature design (markdown)** — defines the 3 drowsiness classes (`Alert` →
   `Low Vigilant` → `Drowsy`), aligned to [UTA-RLDD](https://sites.google.com/view/utarldd/home)'s
   tri-partition of the Karolinska Sleepiness Scale (KSS 1–3 / 6–7 / 8–9) rather than an
   invented scheme, precisely so that dataset's labels can be adopted directly instead of
   re-mapped through a second, harder-to-justify boundary. This project originally used a finer
   6-level scale (1 = alert → 6 = entering microsleep); Argus's own raw clips still keep their
   original `level_<1-6>_clip_<N>.mp4` filenames un-renamed and get mapped down
   (1–2→Alert, 3–4→Low Vigilant, 5–6→Drowsy) at extraction time via a `map_level` helper defined
   in each dataset-creation notebook's Pipeline Configuration Constants cell (identical across
   all three dataset-creation notebooks — keep them in sync). Also defines the candidate
   per-frame feature set: manual EAR/MAR ratios, MediaPipe blendshape scores (eye/mouth/eyebrow),
   gaze, head pose (pitch/yaw/roll), and detector confidence. A "speech confound" flag
   (mouth-smile blendshapes) is tracked separately so talking/singing isn't mistaken for
   yawning. A "Data Collection Methodology" cell right after the levels table documents how the
   labels were actually grounded: Argus's own pre-mapping levels 1–5 are self-recorded under
   genuine fatigue (late-night/sleep-deprived recording sessions, not staged), while level 6 was
   acted — performed while already at a genuine level-5 state, not from a fully alert baseline,
   since safely capturing a real microsleep episode on camera isn't practical. UTA-RLDD ingestion
   (60 subjects, 180 ~10-minute videos, one constant label per video) is a **local script**, not
   a notebook cell — `src/cv-argus/scripts/extract_uta_rldd_clips.py` cuts each source video into
   several short, non-overlapping random sub-clips via `ffmpeg`, writing them as
   `level_<1-3>_clip_<N>.mp4` into new `subject_<N>` folders continuing after Argus's own
   (`subject_07` onward by default) — deliberately reusing the `level_` word (1=Alert,
   2=Low Vigilant, 3=Drowsy, the *final* class) rather than a separate `class_` prefix. Because
   both conventions share the same filename prefix, `map_level` disambiguates by **subject
   number**, not filename: `subject_<N>` with `N >= EXTERNAL_SUBJECT_START` (7) is treated as
   already-final-class; `subject_01`–`subject_06` still gets the original 1–6→3-class mapping.
   Keep that constant in sync with the script's `--start-subject` if it's ever run non-default.
   State the acted-level-6 and cross-dataset-labeling-method caveats plainly in the titulación
   report rather than leaving them implicit.

2. **Google Drive project setup** — mounts Drive and validates/creates the expected folder
   layout under `/content/drive/MyDrive/Argus/`:
   ```
   models/                             # pretrained MediaPipe bundles + trained model weights
   dataset/raw_videos/subject_NN/level_L_clip_NN.mp4
   dataset_processed/lstm_windows.csv        # from 01_dataset_creation_lstm.ipynb
   dataset_processed/frame_features.csv      # from 02_dataset_creation_flat.ipynb
   dataset_processed/face_crops_index.csv    # from 06_dataset_creation_face_crops.ipynb
   dataset_processed/face_crops/*.jpg        # from 06_dataset_creation_face_crops.ipynb
   ```
   Raw video filenames encode subject ID and drowsiness level (`level_<L>_clip_<N>.mp4`), which
   is parsed via regex throughout the notebooks rather than stored as separate structured data.

3. **Exploratory visualization** — histograms/bar charts of clip counts and durations by
   drowsiness level and by subject, to check dataset balance.

4. **MediaPipe FaceLandmarker setup** — downloads the pretrained `.task` bundle; includes a
   (currently disabled/in-progress) quantization step via `ai_edge_quantizer`.

5. **Feature extraction pipeline** — a TensorFlow layer (`GeometricRatioFeatureLayer`) computes
   EAR, MAR, and head pose from landmarks; this is reused both when building training datasets
   and inside the deployed model so behavior is identical in both places. This class must stay
   byte-identical across four places it's redefined: this notebook (source of truth),
   `02_dataset_creation_flat.ipynb`, `08_deployment_export_lstm.ipynb`, and
   `src/cv-argus/src/model/layers.py`. Pipeline config constants (`sampling_fps = 10`, window
   sizes `1.0–6.0s` in 1s steps, `stride_sec = 1.0`, `pose_validity_threshold_deg = 20.0`,
   `MAX_TIMESTEPS = 60`) live in one cell — see "Pipeline Configuration Constants" — and drive
   the sliding-window extraction. Windows containing any invalid (face-lost) frame are discarded
   entirely rather than padded/imputed. Each valid window's `(num_real_frames, 58)` feature
   matrix (7 base geometric features — `EAR_left`, `EAR_right`, `MAR`, `pitch`, `yaw`, `roll`,
   `ear_mar_valid` — followed by 51 MediaPipe blendshape scores) is then zero-*pre*-padded up to
   a fixed `MAX_TIMESTEPS = 60` and flattened into one row of `lstm_windows.csv`, rather than
   saved as a separate `.npy` file per window — thousands of tiny binary files was slow to
   generate and sync. `MAX_TIMESTEPS` was originally 120 (double the 6-second/60-frame max window
   duration, chosen for unused headroom rather than derived from a duration analysis) but was
   brought down to exactly `max_context_sec * sampling_fps = 60` (zero headroom) after a full
   extraction run at 120 OOM-crashed the Colab kernel: the "Video Processing Loop" cell
   accumulates every window's flattened, `MAX_TIMESTEPS * num_features`-wide row from every video
   into one Python list before a single `pd.DataFrame(rows)` call builds the whole CSV at once, so
   peak RAM scales directly with `MAX_TIMESTEPS`. This isn't something `04_random_forest_training.
   ipynb` / `05_dense_nn_training.ipynb` / CNN notebooks need to know about at all — it's purely an
   LSTM sequence-buffer concept; the other three model families consume single-frame/single-image
   rows with no timestep dimension whatsoever.

**`02_dataset_creation_flat.ipynb`** — raw video → one row per valid frame, no windowing:

6. Reuses the same MediaPipe setup, `GeometricRatioFeatureLayer`, and label mapping as notebook
   1, but skips windowing entirely: every valid sampled frame becomes its own row of
   `frame_features.csv` (58 named feature columns + subject/level/parent_video/frame_idx), tagged
   with the single label of the clip it came from. This is deliberately the cheapest dataset to
   generate, for the model families that are structurally incapable of using a sequence anyway.
7. **Statistical validation** — Spearman correlation (levels are ordinal) and Kruskal-Wallis
   tests per feature against drowsiness level, run directly on `frame_features.csv` (no
   window-mean aggregation needed, since each row already is one frame). Used to select
   `04_random_forest_training.ipynb`'s manually-curated feature subset;
   `05_dense_nn_training.ipynb` uses all 58 features instead and lets the network learn its own
   weighting.

**`03_model_training_lstm.ipynb`** — reads `lstm_windows.csv`, doesn't touch video/MediaPipe:

8. **LSTM model** — the primary sequence model. Each CSV row is reshaped straight back into
   `(MAX_TIMESTEPS, num_features)` — no padding decision happens in this notebook, since the CSV
   already stores the padded shape. Architecture:
   `LSTM(128, return_sequences=True) → Dropout → LSTM(64) → Dropout → Dense(3, softmax)`,
   `stateful=False`, `GroupShuffleSplit` grouped by subject (a fix from the original monolith,
   which computed a `groups` variable but then actually split with plain
   `train_test_split(..., stratify=...)`, letting overlapping windows from the same subject leak
   across train/test and inflating reported accuracy), trained with `class_weight` (computed from
   the actual training-label distribution) since `Drowsy` — the one level that had to be acted
   rather than self-recorded, see notebook 1's "Data Collection Methodology" note — is plausibly
   the rarest class and also the most safety-critical to not neglect. A markdown cell right
   before the model definition ("Design Decision: Persistent (`stateful=True`) Hidden State")
   documents why persistent cross-call hidden state was evaluated and deferred rather than
   adopted — worth reading before proposing that change again, since the trade-offs
   (training-pipeline restructuring, a cross-driver state-leak safety risk, and no measured
   latency problem to justify it) were already analyzed there. Padding is zero-*pre*-padded
   (zeros first, real frames last), not post-padded — this has to match how
   `LstmGeometricFeatureModel`'s `feature_buffer` actually fills during the real warm-up in
   deployment (oldest slot dropped, new frame appended at the end); get this backwards and the
   model trains on a padding shape that never occurs in production. This notebook fits and saves
   its own `feature_scaler_*.joblib` (58-feature, per-timestep) — it used to silently reuse the
   RandomForest's differently-shaped scaler of the same name before RandomForest moved out to its
   own notebook, which would have broken deployment export once that happened.

**`04_random_forest_training.ipynb`** — reads `frame_features.csv`:

9. **RandomForest model** — a tabular baseline trained directly on a manually-curated 7-feature
   subset (from notebook 2's correlation ranking) of raw per-frame rows, `class_weight='balanced'`,
   `GroupShuffleSplit` grouped by subject. Saves `rf_classifier_*.joblib` and
   `rf_feature_scaler_*.joblib` — the `rf_` prefix is deliberate, to avoid colliding with the
   LSTM's same-shaped-differently `feature_scaler_*.joblib` name.

**`05_dense_nn_training.ipynb`** — reads `frame_features.csv`:

10. **Dense feedforward NN** — a small fully-connected network (`Dense(128) → Dense(64) →
    Dense(3, softmax)`, `BatchNormalization`/`Dropout` regularization) trained on all 58 raw
    per-frame features (not the RandomForest's curated subset — a NN can learn its own feature
    weighting). Same `GroupShuffleSplit`/`class_weight` conventions as the other notebooks. This
    is *not* a rerun of the deferred windowed-DNN-vs-LSTM comparison described in "Planned
    end-to-end system architecture" above — it's a non-recurrent baseline on the flat dataset,
    same task as RandomForest, not a like-for-like challenger to the LSTM.

**`06_dataset_creation_face_crops.ipynb`** — raw video → cropped face images, no geometric
features at all:

11. Uses a *different*, lighter MediaPipe model bundle than every other notebook — the
    **Face Detector** (`BlazeFace`, bounding-box-only) task, not `FaceLandmarker` — since only a
    crop region is needed here, not landmarks/blendshapes. `BlazeFace` was evaluated against a
    YOLO-based face detector and kept: comparable accuracy for this project's single-frontal-face
    cabin scenario, a smaller edge footprint, and no AGPL licensing exposure (Ultralytics YOLOv8
    ships under AGPL-3.0 unless a commercial license is purchased — a real concern for a project
    meant to become a commercial product) — see `notebook/CLAUDE.md`'s "Recent implementation
    decisions" section for the full comparison. Label-mapping convention matches notebook 2 exactly, but
    `sampling_fps` deliberately does not: this notebook samples much more sparsely
    (`sampling_fps = 1`, capped at `MAX_FRAMES_PER_CLIP` per clip regardless of duration) than the
    geometric-feature notebooks' ~10 FPS, since a CNN training on raw face crops gains little from
    near-duplicate images a few frames apart. Each confidently-detected face is expanded by a
    margin fraction and cropped, written as an individual `.jpg` under
    `dataset_processed/face_crops/` (flat directory, not one-subfolder-per-class — the label
    lives in `face_crops_index.csv` alongside subject/parent_video/frame_idx, so subject-grouped
    splitting stays possible). Each row is appended to that CSV the moment its image is written,
    guarded by a lock shared across the parallel extraction workers, rather than batched per video
    or written once at the end.

**`07_cnn_training.ipynb`** — reads `face_crops_index.csv` + the `.jpg` files:

12. **Tiny CNN** — three `Conv2D → BatchNorm → MaxPool` blocks over `96×96` resized crops,
    `GlobalAveragePooling2D` (not `Flatten`, to keep parameter count down), small dense head.
    Pixel scaling is a `Rescaling` layer inside the model itself rather than a separate
    `StandardScaler` artifact, so the saved `.keras` file is fully self-contained. Same
    `GroupShuffleSplit`/`class_weight` conventions; light augmentation (flip, brightness jitter)
    only, since crops are already tightly framed and aggressive geometric augmentation risks
    distorting the drowsiness-relevant eye/mouth region.

**`08_deployment_export_lstm.ipynb`** — reads the trained LSTM + scaler, doesn't touch training
data:

13. **Deployment model (`LstmGeometricFeatureModel`)** — a custom Keras `Model` subclass that
    wraps the *entire* inference pipeline (raw landmarks/rotation matrix/blendshapes →
    `GeometricRatioFeatureLayer` → concatenation → frame accumulation/padding to
    `max_timesteps` → `Normalization` → trained LSTM → drowsiness level) into one artifact
    saved via Keras' native format, so production code doesn't need to reimplement
    preprocessing separately from training. `max_timesteps` here must equal notebook 1's
    `MAX_TIMESTEPS` (60) — this is the one place outside the LSTM's own two notebooks where that
    constant matters, because it sizes the deployed rolling `feature_buffer`.

14. **End-to-end live-stream simulation** — replays a raw video frame-by-frame through
    MediaPipe + `LstmGeometricFeatureModel` to sanity-check the deployed pipeline end to end.

Model artifacts are versioned by timestamp (`VERSION_STR = ...strftime("%Y%m%d_%H%M")`), with a
`get_latest_model(folder, prefix, extension)` helper (redefined in each notebook's Setup
section) to locate the most recent one and a `FORCE_RETRAIN` flag to control whether existing
models are reused or retrained.

## Model results and current status

This section is the actual empirical record — what's been run, what the numbers were, and what
that implies — as of the latest training runs. Keep it updated as new runs land rather than
letting it drift stale; per this project's own titulación-report standard, describe only what's
actually been measured here, not aspirational numbers.

**RandomForest and Dense NN have hit a real, well-evidenced ceiling around ~33-41% accuracy on
the flat per-frame dataset**, not a tuning shortfall:

- RandomForest baseline (7 curated features, unconstrained trees): **32.6%** accuracy, `Drowsy`
  recall collapsed to 0.13. A regularized/`GroupKFold`-tuned rerun was started but never
  completed — `RandomizedSearchCV(n_jobs=-1)` wrapping `RandomForestClassifier(n_jobs=-1)` double-
  nests parallelism and can thrash instead of finishing; if this is revisited, set only the outer
  `n_jobs=-1` and leave the inner estimator's `n_jobs` at its default.
- Dense NN (all 58 flat features): **38.6-40.8%** across several regularization passes (best:
  heavier dropout/L2 + lower LR). Per-subject feature normalization (z-score within each subject)
  made no real difference (38.4%) — the subject-level-confound hypothesis was checked directly via
  a subject×level crosstab and came back weak (only 2/24 subjects >95% skewed to one class, mean
  max-class share 49%), so that specific explanation is ruled out.
- **Root cause, measured directly, not inferred:** Spearman correlation between individual
  per-frame features and drowsiness level tops out at |r| = 0.26 (`eyeWideRight`); `EAR_mean` —
  the feature the whole geometric pipeline was designed around — sits at |r| = 0.04, essentially
  zero. A single frame's instantaneous eye-openness value doesn't carry much signal on its own;
  the actual tell (duration/velocity of eye closure) is a property of a sequence, not a snapshot.
  This is the same conclusion `03_model_training_lstm.ipynb`'s Design Decision cell already argued
  architecturally, now confirmed statistically.
- **Rolling/temporal feature enrichment made results *worse*, not better** — an important negative
  finding, not just a null result. Adding rolling mean/std (1s and 3s trailing windows) computed
  from `frame_features.csv` pushed Dense NN's accuracy down to 36.7% (154 features) and 29.8%
  (SMOTE + noise-jitter on top of that, actually below chance for 3 classes). Likely mechanism: a
  rolling *mean* over a short single-label clip converges toward that clip's own average value —
  closer to a per-clip fingerprint than genuine moment-to-moment dynamics — which made the
  underlying subject/clip-identity overfitting shortcut *easier* to exploit with 154 columns of
  capacity instead of 58, not harder. The frame-to-frame *delta* (rate-of-change) columns are
  conceptually sound and weren't isolated from the noisier rolling-mean/std ones in this pass; a
  delta-only variant is a plausible follow-up but is not currently planned.
- **Decision:** further RandomForest/Dense NN tuning is paused. `04_random_forest_training.ipynb`
  and `05_dense_nn_training.ipynb` are left as-is (including the incomplete/hung search cell and
  the enrichment experiments that underperformed) as the honest record of what was tried.

**Next direction: CNN on face crops, possibly combined with the LSTM's windowing.** Two paths
exist in the repo, at different stages of readiness — neither has been run yet:

- `06_dataset_creation_face_crops.ipynb` + `07_cnn_training.ipynb` — single face-crop image → single
  label (see "Notebook architecture" above). Built, not yet run. Expected to face the same
  single-instant-in-time ceiling as RF/Dense NN in principle, tempered by the fact that raw pixels
  carry visual cues (skin texture, redness, micro-expression detail) the hand-engineered
  EAR/MAR/blendshape features don't capture at all — plausibly better than 40%, not guaranteed to
  be dramatically better, since it's still one frame.
- `09_dataset_creation_cnn_lstm.ipynb` — a fifth model family: `TimeDistributed(CNN) → LSTM` over
  an actual **windowed sequence** of face-crop images (same 1-6s/1s-stride multi-duration scheme as
  the geometric LSTM), rather than a single image. This is the most complete fix for the
  single-frame ceiling among the vision-based options, since it combines real temporal context
  with raw-pixel visual cues. Built as a **dataset-index step only** — it reuses `06`'s
  already-extracted crops (no re-extraction) and just builds `cnn_lstm_windows_index.csv`,
  referencing existing crop files by a window's frame range rather than duplicating them. Padded
  to `MAX_TIMESTEPS_IMG = 60` — a separately-tunable constant from the geometric LSTM's own
  `MAX_TIMESTEPS` (also currently 60, after being brought down from 120 for RAM reasons — see
  "Notebook architecture" above). The two now happen to match, but for unrelated reasons: this one
  was chosen because padding cost scales with tensor size — cheap for a 58-float row, expensive
  for a full image — so there's no headroom-padding upside here worth doubling memory/compute for.
  **The training notebook for this (`10_cnn_lstm_training.ipynb`) does not exist yet.** Also worth
  flagging plainly: this would be by far the most data-hungry model in the project (a deep model
  over raw pixel *sequences*, ~24 subjects) — a real architecture upgrade, but not a guaranteed
  win on a dataset this size, and should be evaluated with that expectation going in.

## Working in this repo

- Treat the eight-notebook split as the source of truth for the pipeline; when asked to change
  feature extraction, windowing, or model logic, edit the corresponding cell(s) rather than
  assuming there is equivalent code elsewhere.
- Cell execution order matters *within* a notebook — cells reference variables
  (`project_folder`, `models_folder`, `video_files`, `face_landmarker_options`, etc.) defined
  earlier in the same linear run. Across notebooks, only Drive artifacts carry over, not
  variables — don't assume one notebook can see another's in-memory state.
- Paths are hardcoded to Google Drive (`/content/drive/MyDrive/Argus/...`); there is no local
  dataset checked into this repo.
- `GeometricRatioFeatureLayer` is intentionally redefined verbatim in four places
  (`01_dataset_creation_lstm.ipynb`, `02_dataset_creation_flat.ipynb`,
  `08_deployment_export_lstm.ipynb`, `src/cv-argus/src/model/layers.py`) because Keras requires
  the exact same class to be importable to deserialize a saved model — there's no automated check
  that all four stay in sync, so a change to one needs manual verification against the other
  three. `06_dataset_creation_face_crops.ipynb` does *not* use this class — it only needs a face
  bounding box, via MediaPipe's separate, lighter Face Detector task.
- `MAX_TIMESTEPS` (the LSTM's fixed padded-window length, currently 60) is an LSTM-only concept.
  It has no bearing on RandomForest, the Dense NN, or the CNN — those three consume single-frame
  or single-image rows with no timestep dimension at all, from a dataset (`frame_features.csv` /
  `face_crops_index.csv`) that was never windowed in the first place.
