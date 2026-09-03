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
- A camera captures frames, read by the **Raspberry Pi 5 ("AI Orchestrator")**, which — per the
  design this section describes — runs the MediaPipe FaceLandmarker + geometric-ratio-layer +
  sequence-model pipeline from the notebook and produces a drowsiness class. **The class scheme
  is now binary — `Not Drowsy` vs. `Drowsy`** (decided; `Not Drowsy` = the old `Alert` +
  `Low Vigilant` merged, `Drowsy` = the old `Drowsy`). The earlier 3-class `Alert` /
  `Low Vigilant` / `Drowsy` scheme is retired: `Low Vigilant`, the ambiguous middle class, was
  the single most consistently broken class across every model family (0.0000 recall in two
  separate runs), so merging it away removes a real failure mode rather than just raising the
  random-guess floor to 50%. All ten notebooks are migrated in code; the Colab reruns that
  regenerate the datasets/models under the binary labels are still pending, and `src/cv-argus`
  keeps the 3-class names until a binary model is actually trained and deployed — see the
  "Working in this repo" section and `notebook/CLAUDE.md`'s "Binary migration" for status.
  **That sequence-model pipeline is the long-term intended architecture, not what's currently
  deployed**: `src/cv-argus` currently focuses on and downloads/runs the CNN face-crop model by
  default instead (MediaPipe Face Detector/BlazeFace crop → CNN, not FaceLandmarker → LSTM) — a
  current implementation decision, not a reversal of the design reasoning below. See
  `src/cv-argus/CLAUDE.md`'s "Current status" for the deployed default and
  `notebook/CLAUDE.md`'s "Why CNN is the most probable next backbone" for why. Keep this
  distinction — *intended long-term design* vs. *what's actually deployed right now* — straight
  when reading the rest of this bullet. The sequence model is an **LSTM**; this had briefly
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
  travel management, and reports. (The `react-leaflet` map is now implemented in `src/ui-argus`
  — Live operations screen, standard OpenStreetMap tiles, markers from fixture coordinates;
  OSRM route-line rendering is still deferred. The map-library choice — react-leaflet over
  Google Maps / Amazon Location — and how backend coordinates get normalized are written up in
  `docs/designs/frontend-map-and-coordinates.md`.)
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

- `src/notebook/01_dataset_creation_lstm.ipynb` through `src/notebook/10_cnn_lstm_training.ipynb`
  (the `notebook/` folder was moved under `src/`) — the ML pipeline, split into ten stage-scoped
  notebooks across four model families (LSTM, RandomForest, Dense NN, face-crop CNN / CNN+LSTM)
  that share dataset-creation stages. See `src/notebook/CLAUDE.md` for what each one does and how
  they hand off. `src/notebook/ArgusMLModel.ipynb` is the retired monolith. The **training**
  notebooks (`03`/`04`/`05`/`07`/`10`) run in Google Colab (Drive as storage backend, GPU);
  validate changes by reasoning about the cells or running them in Colab.
- `src/dataset/` — **local, CPU-parallel, pausable/resumable reimplementation of the four
  dataset-creation notebooks** (`01`/`02`/`06`/`09`), built to run in WSL2 instead of Colab
  (which kept interrupting the multi-hour extraction runs). This is now the source of truth for
  dataset creation; those four notebooks are kept as Colab reference. It reads
  `src/dataset/raw/raw_videos/` directly (clips expected already binary-labelled `level_1` /
  `level_2`) — no relabel step, unlike the Colab flow.
  Standalone module (`mediapipe` + `opencv` + `numpy` + `pandas` + `tqdm`; **no TensorFlow** — the
  `GeometricRatioFeatureLayer` maths is reimplemented in NumPy in `argus_dataset/geometry.py`,
  equivalence-tested against the real layer). Constants that must match the notebooks live in
  `argus_dataset/config.py`, each annotated with its notebook + cell. Has a `README.md`
  (how to run it) and a `CLAUDE.md` (architecture, the pause/resume design, the
  notebook-fidelity contract — read it before changing anything here). Artifacts are pushed to
  Drive via `scripts/publish_to_drive.py` for the Colab training notebooks.
- `src/cv-argus/` — the Raspberry Pi 5 edge module: loads a trained model from the notebook and
  runs live inference against camera frames, end to end (camera/video-file source → MediaPipe →
  model inference → an output sink), not just the model-loading piece. **Currently focused on
  and deploying the CNN face-crop model by default** (`PIPELINE=cnn`), not the LSTM described
  above as the long-term intended design — the LSTM path is kept and fully functional
  (`PIPELINE=lstm`), just not the default. Built as a threaded, queue-connected `Stage`/
  `Pipeline` abstraction (see its `CLAUDE.md`'s "`pipeline/` — done") specifically so both model
  families — and any future one — share the same camera-loop/threading/shutdown plumbing rather
  than each reimplementing it; also has a demo mode (a browser-viewable live video stream with
  the drowsiness classification overlaid, see that file's "Demo" section) for actually watching
  it work on a laptop or the Pi. Docker-first (own `Dockerfile`/`docker-compose.yml`, plus a
  `docker-compose.pi.yml` overlay for the Pi's CSI camera). Has both a `README.md` (practical
  "how do I run this" — quick start, the demo, a config-variable table, troubleshooting) and a
  `CLAUDE.md` (architecture, why things are built the way they are, notebook-fidelity
  requirements); read the `CLAUDE.md` before *changing* anything in this module, not just
  running it — it has the details (exact model input/output shapes, why the model classes must
  be ported verbatim, etc.) that would otherwise need re-deriving from the notebook each session.
- `docs/argus-descripción-proyecto.pdf` — project description/proposal.
- `docs/criterios/` — academic thesis/grading-criteria documents (this is a school "trabajo de
  grado" project); `Formato_Proyecto_Modular V2.docx` is the report template being filled in.
- `docs/designs/semantic-design*` — draw.io system architecture diagram; source of truth for
  the planned end-to-end architecture summarized above (`semantic-design` is the raw XML,
  `semantic-design.drawio.png` a rendered export — re-render after editing the XML).
- `docs/references/` — background research papers on drowsiness/microsleep detection that
  inform feature and model choices.

**Current deployment status, stated plainly since it's easy to lose track of amid the caveats
above: `src/cv-argus` is currently focused on and deploys the single-frame CNN
(`07_cnn_training.ipynb`'s model) by default** (`PIPELINE=cnn`) — not the LSTM, despite the LSTM
remaining the architecturally-intended long-term model per the reasoning in "Planned end-to-end
system architecture" above, **and not the CNN+LSTM either.** An earlier stale-24-subject smoke
test of the CNN+LSTM had briefly looked like the project's best measured result (46.79% accuracy
/ 0.4090 macro-F1, MobileNetV2 frozen) — that number does **not** hold up: the full-54-subject-
pool rerun that `notebook/CLAUDE.md`'s "What we found" already called for dropped it to 33.68%
accuracy / 0.3340 macro-F1, below both Dense NN (38.6–40.8%) and the deployed single-frame CNN
(36.67%/0.3614) — so the CNN+LSTM was never actually the best measured result on a trustworthy
run, and staying on the single-frame CNN was the right call for reasons beyond the ones below.
`09`/`10` were then modified again (geometric feature fusion, `AdamW`/`recurrent_dropout`
regularization, `class_weight=None`) and have now actually been rerun — the from-scratch backbone
reached 35.04% accuracy / 0.3296 macro-F1, still just under the deployed single-frame CNN
(36.67%/0.3614), not a decisive win. MobileNetV2 overfit even more severely on this rerun (95%+
train accuracy, `val_macro_f1` never above 0.36) than on any prior attempt and has been disabled
(commented out, not deleted) in `10` as a result — it has never once beaten the from-scratch
backbone on a trustworthy run in this project. See `notebook/CLAUDE.md`'s "What we found" for the
full numbers. This is a current decision, not a claim that the accuracy caveats just described are
resolved — the deployed CNN's real,
measured number (36.67% accuracy, 0.3614 macro-F1 single-fold; 35.93% ± 9.07% across the 3-fold
subject-CV diagnostic, per the reruns above, still against the earlier 24-subject pool) is itself
a low-recall, small-N result with a real train/test generalization gap, not a validated
production number either. The LSTM path is kept in `src/cv-argus`
and fully functional (`PIPELINE=lstm`), just not the default. See `src/cv-argus/CLAUDE.md`'s
"Current status" for the deployment-side detail and `notebook/CLAUDE.md`'s "Why CNN is the most
probable next backbone" for the reasoning.

## Working in this repo

- **The drowsiness class scheme is binary: `Not Drowsy` vs. `Drowsy`** (`drowsy_vs_not` —
  `Not Drowsy` = old Alert + Low Vigilant merged, `Drowsy` = old Drowsy). This is the settled
  decision, not one option among several. The **code** side is complete: all ten notebooks
  (`01`–`10`, dataset creation + training + export) are migrated — `CLASS_NAMES =
  ["Not Drowsy", "Drowsy"]`, `NUM_CLASSES = 2`, `== {1, 2}` fold guards — and the
  dataset-creation ones read a new `dataset/raw_videos_binary/` tree. What has **not** happened is
  any rerun — every notebook still loads its old 3-class Drive artifacts, so all results and
  framing in these files are the pre-migration record. `src/cv-argus`'s deploy-time class names
  are deliberately still 3-class (no binary `.keras` model exists to deploy yet). See
  `notebook/CLAUDE.md`'s "Binary migration" for the authoritative per-notebook status.
- For **training** logic (`03`/`04`/`05`/`07`/`08`/`10`) the notebooks are the source of truth;
  edit the corresponding cell(s). For **dataset creation** (`01`/`02`/`06`/`09`) the source of
  truth is now `src/dataset/` — edit `argus_dataset/config.py` + the relevant module there, and
  mirror the constant into the matching notebook (kept as Colab reference).
- **`07` and `11_cnn_lstm_training_drive_pull` now choose a `Drowsy` decision threshold** (not
  `argmax`) on the validation set after training and write it to `<checkpoint>.keras.threshold.json`
  next to the model. When a binary model is deployed, `src/cv-argus` should read that file and
  threshold `p(Drowsy)` instead of taking the softmax argmax. Selection is safety-first: hit a
  `Drowsy` recall floor, then maximise precision. `11_drive_pull` also had a learning-rate bug
  (both models trained at ~1e-8) that made its encouraging in-training `val_macro_f1` a
  non-training artifact — fixed; see `notebook/CLAUDE.md`. Minority-class window rebalancing
  (Drowsy windows overlap-tiled in `09`/`src/dataset`) is code-done, rerun pending.
- Cell execution order matters *within* a notebook — cells reference variables
  (`project_folder`, `models_folder`, `video_files`, `face_landmarker_options`, etc.) defined
  earlier in the same linear run. Across notebooks, only Drive artifacts carry over, not
  variables — don't assume one notebook can see another's in-memory state.
- In the **notebooks**, paths are hardcoded to Google Drive (`/content/drive/MyDrive/Argus/...`).
  The **local** `src/dataset/` pipeline uses `$ARGUS_DATASET_ROOT` (default: the `src/dataset/`
  dir) with `raw/`, `processed/`, `models/` beneath it, all gitignored. No dataset is checked in.
- `GeometricRatioFeatureLayer` is intentionally redefined verbatim in five places
  (`01_dataset_creation_lstm.ipynb`, `02_dataset_creation_flat.ipynb`,
  `08_deployment_export_lstm.ipynb`, `src/cv-argus/src/model/layers.py`, and now
  `09_dataset_creation_cnn_lstm.ipynb`, which uses it to compute a 10-feature EAR/MAR/blendshape
  fusion input for the CNN+LSTM model — see that notebook's "Geometric Features for Fusion"
  section) because Keras requires the exact same class to be importable to deserialize a saved
  model — there's no automated check that all five stay in sync, so a change to one needs manual
  verification against the other four. `06_dataset_creation_face_crops.ipynb` itself does *not*
  use this class — it only needs a face bounding box, via MediaPipe's separate, lighter Face
  Detector task; `09` runs `FaceLandmarker` separately, directly on `06`'s saved crop images,
  specifically so `06` doesn't need to be re-run to get this fusion feature. The local
  `src/dataset/` pipeline does **not** add a sixth copy: `argus_dataset/geometry.py` is a NumPy
  reimplementation of the same maths (the layer is only *called*, never deserialized, during
  dataset creation), kept honest by `src/dataset/tests/test_geometry_equiv.py` which diffs it
  against `layers.py`'s real layer to `atol=1e-4`.
- `MAX_TIMESTEPS` (the LSTM's fixed padded-window length, currently 30 — it was 60 until
  `01`/`02`'s `sampling_fps` dropped from 10 to 5, making a 6s window 30 frames) is an LSTM-only concept.
  It has no bearing on RandomForest, the Dense NN, or the CNN — those three consume single-frame
  or single-image rows with no timestep dimension at all, from a dataset (`frame_features.csv` /
  `face_crops_index.csv`) that was never windowed in the first place.
