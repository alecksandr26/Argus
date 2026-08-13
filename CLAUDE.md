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
  MediaPipe FaceLandmarker + geometric-ratio-layer + LSTM pipeline from the notebook and
  produces a drowsiness level (1–6). A steering-wheel grip sensor feeds the same decision
  orchestration node. Outgoing alert/status events are written to a local **SQLite queue/buffer**
  so nothing is lost when connectivity drops (this satisfies the "Memoria Local (Buffer)" MVP
  requirement — buffered events are auto-resent once a connection is available).
- A separate **ESP32 ("Message Sender Orchestrator")** reads that buffer and owns everything
  actuation- and safety-critical: it drives the **alarm speaker**, the **CAN Bus/AEB actuator**
  (preventive autonomous braking), reads the **panic button** and **geolocation module**, and is
  the device that talks to the internet (protocol not yet decided — candidate: HTTPS).
- Deliberate split: the Pi *decides* (heavy AI inference, containerizable, can be redeployed via
  OTA), the ESP32 *acts* (bare-metal/real-time, must not depend on a Linux/Docker boot cycle
  completing). Don't move CAN-bus/alarm/panic-button logic onto the Pi — keep that boundary.

**Cloud/server side (planned as Docker Compose):**
- A **backend API** (Django, or Flask/FastAPI) exposing REST resources for `users`, `trucks`,
  `drivers`, `alerts`, `routes`, and `routes/:id/status`, backed by a SQL or MongoDB database
  (not yet decided).
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
via libcamera, or `/dev/ttyAMA0`/`/dev/ttyUSB0` if the Pi↔ESP32 link is serial rather than
Wi-Fi/MQTT) rather than `--privileged`. The CSI-camera-through-Docker path is the one detail
worth prototyping early, since libcamera device passthrough is fussier than a USB webcam.

## Repository state and structure

Only two parts of the planned architecture exist as code so far: the training notebook and
the `cv-argus` edge module (below). The backend, frontend, Docker Compose stack, and ESP32
firmware described in "Planned end-to-end system architecture" don't exist yet.

- `notebook/ArgusMLModel.ipynb` — the entire ML pipeline: dataset assembly, feature
  extraction, statistical validation, and model training/evaluation. This is the file to read
  and edit for almost any task involving the model itself. There is no build system, package
  manifest, linter, or test suite for it — it's designed to run in Google Colab (mounts Google
  Drive as its storage backend), not as a local script; validate changes by reasoning about the
  notebook cells or, if actually executing it, running it inside Colab (or a Jupyter
  environment with `mediapipe`, `opencv-python`, `tensorflow`, `scikit-learn`, `pandas`,
  `joblib` installed) rather than assuming a local CLI workflow exists.
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

## Notebook architecture (`notebook/ArgusMLModel.ipynb`)

The notebook is organized as a linear pipeline; later sections depend on variables/objects
defined by earlier ones (this is Colab-style top-to-bottom state, not a package with imports).
The stages, in order:

1. **Labels & feature design (markdown)** — defines the 6 drowsiness levels (1 = alert →
   6 = entering microsleep) and the candidate per-frame feature set: manual EAR/MAR ratios,
   MediaPipe blendshape scores (eye/mouth/eyebrow), gaze, head pose (pitch/yaw/roll), and
   detector confidence. A "speech confound" flag (mouth-smile blendshapes) is tracked
   separately so talking/singing isn't mistaken for yawning.

2. **Google Drive project setup** — mounts Drive and validates/creates the expected folder
   layout under `/content/drive/MyDrive/Argus/`:
   ```
   models/                        # pretrained MediaPipe bundle + trained model weights
   dataset/raw_videos/subject_NN/level_L_clip_NN.mp4
   dataset_processed/features/*.npy   # per-window feature matrices
   dataset_processed/metadata.csv     # master index (subject, level, window duration, dropped frames)
   ```
   Raw video filenames encode subject ID and drowsiness level (`level_<L>_clip_<N>.mp4`), which
   is parsed via regex throughout the notebook rather than stored as separate structured data.

3. **Exploratory visualization** — histograms/bar charts of clip counts and durations by
   drowsiness level and by subject, to check dataset balance.

4. **MediaPipe FaceLandmarker setup** — downloads the pretrained `.task` bundle; includes a
   (currently disabled/in-progress) quantization step via `ai_edge_quantizer`.

5. **Feature extraction pipeline** — a TensorFlow layer (`GeometricRatioFeatureLayer`) computes
   EAR, MAR, and head pose from landmarks; this is reused both when building the training
   dataset and inside the deployed model so behavior is identical in both places. Pipeline
   config constants (`sampling_fps = 10`, window sizes `1.0–6.0s` in 1s steps, `stride_sec =
   1.0`, `pose_validity_threshold_deg = 20.0`) live in one cell — see "Pipeline Configuration
   Constants" — and drive the sliding-window extraction. Windows containing any invalid
   (face-lost) frame are discarded entirely rather than padded/imputed. Each valid window is
   written as a `.npy` array of shape `(num_frames, 59)`: 7 base geometric features
   (`EAR_left`, `EAR_right`, `MAR`, `pitch`, `yaw`, `roll`, `ear_mar_valid`) followed by 52
   MediaPipe blendshape scores.

6. **Statistical validation** — Spearman correlation (levels are ordinal) and Kruskal-Wallis
   tests per feature against drowsiness level, used to justify which features feed the models.

7. **RandomForest model** — a tabular baseline trained with `GroupKFold` (grouped by subject,
   to prevent leakage across a subject's clips) and `StandardScaler`.

8. **LSTM model** — the primary sequence model trained on the windowed `.npy` features.

9. **Deployment model (`LstmGeometricFeatureModel`)** — a custom Keras `Model` subclass that
   wraps the *entire* inference pipeline (raw landmarks/rotation matrix/blendshapes →
   `GeometricRatioFeatureLayer` → concatenation → frame accumulation/padding to
   `max_timesteps` → `Normalization` → trained LSTM → drowsiness level) into one artifact
   saved via Keras' native format, so production code doesn't need to reimplement
   preprocessing separately from training.

10. **End-to-end live-stream simulation** — replays a raw video frame-by-frame through
    MediaPipe + `LstmGeometricFeatureModel` to sanity-check the deployed pipeline end to end.

Model artifacts are versioned by timestamp (`VERSION_STR = ...strftime("%Y%m%d_%H%M")`), with a
`get_latest_model(folder, prefix, extension)` helper to locate the most recent one and a
`FORCE_RETRAIN` flag to control whether existing models are reused or retrained.

## Working in this repo

- Treat the notebook as the source of truth for the pipeline; when asked to change feature
  extraction, windowing, or model logic, edit the corresponding cell(s) rather than assuming
  there is equivalent code elsewhere.
- Cell execution order matters — cells reference variables (`project_folder`, `models_folder`,
  `video_files`, `face_landmarker_options`, etc.) defined earlier in the same linear run.
- Paths are hardcoded to Google Drive (`/content/drive/MyDrive/Argus/...`); there is no local
  dataset checked into this repo.
