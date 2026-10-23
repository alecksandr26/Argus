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
grading criteria (`docs/criteria/criteriosaprobacion_0.pdf`), which require covering three
modules: **Arquitectura y Programación** (justified language/data-structure/methodology
choices, system modeling), **Sistemas Inteligentes** (ML/CV with a justified mathematical
model), and **Sistemas Distribuidos** (a genuinely decentralized system — not just a UI
consuming a centralized service — with real-time sync between at least two devices and
justified communication protocols). Keep this in mind when proposing designs: a suggestion
that collapses the distributed edge/cloud split into a single monolith would satisfy the
product but not the degree requirement.

### Planned end-to-end system architecture

The full system (see `docs/designs/semantic-design.drawio.png` / the underlying `.drawio` XML
at `docs/designs/semantic-design`) has two halves — the edge (truck cabin) and the cloud/server
side. Both halves now have real code behind them (see "Repository state and structure" below
for exactly what and its verified/unverified status); OSRM is the one piece of this section
that remains pure design, not implemented anywhere in this repo. `src/esp32-argus` now has
real firmware too (see that module's README.md) — written, but not yet compiled or run
against real hardware, since no ESP32/SIM800L/NEO-6M harness has been available in any
session that's touched this repo.

**Truck cabin (edge, hard real-time / safety-critical):**
- A camera captures frames, read by the **Raspberry Pi 5 ("AI Orchestrator")**, which — per the
  design this section describes — runs the MediaPipe FaceLandmarker + geometric-ratio-layer +
  sequence-model pipeline from the notebook and produces a drowsiness class. **The class scheme
  is now binary — `Not Drowsy` vs. `Drowsy`** (decided; `Not Drowsy` = the old `Alert` +
  `Low Vigilant` merged, `Drowsy` = the old `Drowsy`). The earlier 3-class `Alert` /
  `Low Vigilant` / `Drowsy` scheme is retired: `Low Vigilant`, the ambiguous middle class, was
  the single most consistently broken class across every model family (0.0000 recall in two
  separate runs), so merging it away removes a real failure mode rather than just raising the
  random-guess floor to 50%. All ten notebooks are migrated in code, and `src/cv-argus` now
  deploys binary labels too (`Not Drowsy`/`Drowsy`) — see the "Working in this repo" section and
  `notebook/CLAUDE.md`'s "Binary migration" for the per-notebook rerun status.
  **Update (2026-10-18): `src/cv-argus` no longer deploys any classifier described in this
  bullet.** It now runs notebook 12's tiny YOLO "parts" detector (eyes open/closed, yawn, head
  straight/down) plus an IoU tracker and deterministic timed rules that output a severity (see
  "Current deployment status" below and `src/cv-argus/CLAUDE.md`). The CNN+LSTM described next
  is preserved on branch `archive/cnn-lstm-fused` and stays the thesis's comparison point; read
  the rest of this bullet as that history.
  **That sequence-model pipeline is the long-term intended architecture; what was deployed
  until 2026-10-18 is a related but distinct shape**: `src/cv-argus` deployed a **binary
  frozen-CNN-embedding + geometric-feature-fusion + LSTM classifier** — a MediaPipe Face
  Detector/BlazeFace crop → a frozen **convolutional** network's 64-dim embedding, fused
  per-timestep with a FaceLandmarker geometric-feature subset (EAR/MAR ratios + selected
  blendshapes) → an **LSTM** over a rolling window, output `Not Drowsy` vs. `Drowsy` — not the
  original FaceLandmarker → windowed-geometric-features-only → LSTM design this section
  describes. A real result finally beat that original plan's absence with a validated
  architecture of its own, rather than settling for the single-frame CNN that had been the
  default before it. See `src/cv-argus/CLAUDE.md`'s "Current status" for the deployed
  pipeline's real numbers and open caveats, and `notebook/CLAUDE.md`'s "`11_
  cnn_lstm_training_drive_pull.ipynb`'s fixed rerun" section for where the underlying result
  came from. Keep this
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
  current status.
  Outgoing alert/status events are written to a local **SQLite queue/buffer** — this is the
  edge-side alert queue only, separate from the cloud database below — so nothing is lost when
  connectivity drops (this satisfies the "Memoria Local (Buffer)" MVP requirement — buffered
  events are auto-resent once a connection is available).
- A separate **ESP32 ("Message Sender Orchestrator")** reads that buffer and owns everything
  actuation- and safety-critical: it drives the **alarm speaker**, the **CAN Bus/AEB actuator**
  (preventive autonomous braking), reads the **geolocation module** (every 1 s, with a last-known
  position kept in NVS) and the **steering-wheel grip sensor**, polls the Pi's SQLite buffer over
  **Bluetooth** (the ESP32 initiates every session — the Pi doesn't push; since the final tweaks
  this is **protocol v2**: `PULL n` / `DLQ 1` / `CONFIG`, the Pi answers `END <dlq> <outbox>`, the
  ESP32 reports `ACK` / `TAKEN` (a 120 s lease) / `FAIL <id> <reason> <body>` and ends with
  `DONE` → `OK ...`; v2 is not compatible with v1, so the Pi image and the firmware deploy
  together — see `docs/designs/final-tweaks-contracts.md` C1), and is the device that talks
  to the backend over **HTTP** — one combined report per POST (`POST /api/devices/me/report`:
  the GPS points since the last report plus at most one event, every 5 s or at once for an
  event, live criticals first and dead-letter replays last), authenticated with a per-truck device API key rather than a user
  login, now that `src/backend-argus` exists (see that module's `CLAUDE.md`, "Device (ESP32)
  auth"). That key is issued from the dashboard (Fleet → "Generate device credentials"), held by
  the Pi (`argus-device.env`), and fetched by the ESP32 over Bluetooth (`CONFIG`), so no
  per-truck value is flashed into the firmware — see `src/esp32-argus/README.md` section 1b.
  **The panic button is gone** (removed in the final tweaks, with its NVS queue and the panic
  SMS; the SIM800L stays for mobile data only). The grip sensor does **not** feed the Pi's
  AI-orchestrator decision node — it and geolocation both wire directly to the ESP32 (see
  `docs/designs/semantic-design-overview.md`), independent of the camera pipeline. This makes the
  ESP32 the actual fusion point for drowsiness + grip: it **fuses** the Pi's relayed drowsiness
  classification with its own live grip reading via a documented debounce/escalation/recovery
  state machine (not a plain 1:1 relay of every drowsiness alert it pulls) — see
  `src/esp32-argus/README.md`'s fusion section for the full decision spec, and
  `src/cv-argus/src/orchestrator/fusion_contract.py` for its typed reference contract. There is
  no grip sensor on the bench yet, so the firmware's `fusion.*` is kept but unused and the Pi's
  severity decides on its own.
- Deliberate split: the Pi *decides* (heavy AI inference, containerizable, can be redeployed via
  OTA), the ESP32 *acts* (bare-metal/real-time, must not depend on a Linux/Docker boot cycle
  completing). Don't move CAN-bus/alarm logic onto the Pi — keep that boundary.

**Cloud/server side (Docker Compose):**
- A **backend API** (**FastAPI**) exposing REST resources for `users`, `trucks`, `drivers`,
  `alerts`, `routes`, and `routes/:id/status`, backed by **MongoDB** accessed through **Beanie**
  (an async ODM) rather than raw driver calls. **This now exists as code** — `src/backend-argus`
  — covering exactly those six resources plus login; see that module's `CLAUDE.md` for the full
  design (auth, RBAC, geo storage) and "Repository state and structure" below for status.
  Since the final tweaks it also has **incidents** (medium/critical alerts of one route within
  15 min of the incident's last alert form one incident; only `guardian` and `root_admin` close
  one, and a critical needs a note), a per-route `incident_state` (critical/medium/fine, which
  colours the map marker), `POST /api/routes/{id}/stop` (a reason is required; afterwards every
  device write for that route is rejected), `GET /api/live` (the dashboard's single 5 s poll) and
  `GET /api/routes/{id}/track`, and the device's one write, `POST /api/devices/me/report`. The
  old device writes (`POST /api/alerts`, `POST /api/routes/{id}/status`, the device-key resolve)
  still answer for old firmware and are removed in a later cleanup (contracts C2.4).
  `Report`, `Device`, and `Geofence` are in the ER diagram but stay future work (no consumer
  committed to them yet).
- An **OSRM** (`Project-OSRM/osrm-backend`) container computing routes from a prebaked
  OpenStreetMap extract, queried by the backend/frontend for route + ETA data. **Still deferred**
  — not part of `src/backend-argus`'s Docker Compose stack, per the borrador doc's explicit
  "mejora futura" framing.
- A **React** frontend using **react-leaflet** to render the OSRM route and live truck/alert
  status, with panels for login, fleet management, driver/user access, route tracking, alerts,
  travel management, and reports. (The `react-leaflet` map is now implemented in `src/ui-argus`
  — Live operations screen, standard OpenStreetMap tiles, markers from the real backend's
  `GET /api/live` (polled every 5 s, paused while the tab is hidden; the fixture data this used
  to read from is gone — see "Repository state and structure" below), marker colour from the
  route's `incident_state`, alert pins and the GPS path; OSRM route-line rendering is still
  deferred. The map-library
  choice — react-leaflet over Google Maps / Amazon Location — and how backend coordinates get
  normalized are written up in `docs/designs/frontend-map-and-coordinates.md`.)
- **Four actor roles** (grew from three — `admin` was added later as a deliberate product
  decision, not something any design doc anticipated), a settled enum matching
  `src/backend-argus`'s `Role`: **`root_admin`** (the owner/bootstrap account — manages users of
  any role, trips, reports), **`admin`** (an operations role scheduling routes and managing the
  truck/driver roster, with one narrow, server-enforced exception to otherwise-zero
  user-management access: it may create/edit/deactivate `guardian`-role accounts only, never
  `root_admin`/other-`admin` accounts; it can also complete or stop a route, but not close
  incidents), **`guardian`** (monitoring + closing incidents, the only write it has),
  **`truck_driver`** (receives alerts/status) — see `src/backend-argus/CLAUDE.md`'s RBAC table
  for the full per-resource matrix and exactly how `admin`'s scoping is enforced.

When implementing the edge side, containerize the Pi's AI-orchestrator service (reproducible
MediaPipe/TensorFlow/OpenCV versions, easy redeploys) but pass through specific devices
(`--device=/dev/video0`, the relevant `/dev/video*`/`/dev/dma_heap/*` nodes for the CSI camera
via libcamera, or whatever Bluetooth access the container needs for the Pi↔ESP32 link — e.g. a
`/dev/rfcommN` device node, or the host's BlueZ/D-Bus socket — now that Pi↔ESP32 is decided as
Bluetooth rather than serial or Wi-Fi/MQTT) rather than `--privileged`. The CSI-camera-through-
Docker path is the one detail worth prototyping early, since libcamera device passthrough is
fussier than a USB webcam.

## "Missing alerts" (dead-lettered events): what the final tweaks built, and what's still open

Agreed with the user on 2026-10-20 as a feature to plan before building; planned and built in the
**final tweaks** (`docs/designs/final-tweaks-plan.md`, contracts in
`docs/designs/final-tweaks-contracts.md`, team branches `feat/final-tweaks-t1-…` to `-t6-…` merged into
`feat/final-tweaks`). A "missing alert" is a real event that happened while the truck couldn't
reach the backend and arrives late.

**Built now (code + tests; no hardware run yet):**
- **Pi (`cv-argus`, T1):** "live first, dead-letter on failure". New events go to the outbox and
  are leased to the ESP32 (`TAKEN`, 120 s). Only a **critical** the ESP32 couldn't deliver within
  its 60 s live window goes to the **DLQ**, together with the event body the ESP32 composed then
  (event-time GPS position, speed, scores). Mediums and driver-OK messages are dropped after
  30 s instead. The DLQ is replayed **one row per session, newest first, round-robin**, and only
  when no live event is pending. Every attempt counts, including ones made with no network; after
  **5** the row moves to a Pi `archive` table (never deleted, never resent). `route_finished`,
  `no_route` and `too_old` rejections archive at once. Existing non-critical and `route_status`
  DLQ rows are migrated to the archive.
- **ESP32 (`esp32-argus`, T3):** a single sender task with a fixed priority (live critical >
  live medium > live OK > DLQ replay). Any live event pre-empts a replay, which then goes back to
  the DLQ without counting the attempt. A replay is sent with `delivery: "dlq"` and
  `queued_seconds`, its original position, and an `age_s` from which the backend computes the
  original time. Logs are tagged `[live]` / `[dlq]`.
- **Backend (`backend-argus`, T4):** `Alert` stores `delivery` (`live` | `dlq`) and
  `queued_seconds`. The event's route is chosen **by time**: the truck's route whose
  departure..arrival contains `occurred_at`. An event after the route ended is rejected
  `route_finished`, which archives it on the Pi. A late alert joins an open incident only if it
  is within 15 min of it by `occurred_at`, and never joins a closed one. Deduplication by
  `(id_route, client_event_id)` makes a retried POST harmless. GPS points of a failed report are
  dropped, not replayed, so no late point can overwrite a newer "latest position".
- **UI (`ui-argus`, T5):** a replayed alert carries a **"Recovered after lost connection"** badge,
  on its card, on map pins and in history, and its incident is flagged `has_replayed`. It shows
  in the incident feed with its incident; a stale incident falls into the **Old** tier (more
  than 1 h since its last alert) instead of looking current. Guardians close it like any other
  incident (a note is required for a critical).
- **Simulator (`simulator-argus`, T6):** `SIMULATOR_OUTAGE_CHANCE` /
  `SIMULATOR_OUTAGE_AROUND_CRITICAL` produce a dead-lettered critical and its replay for demos.

**Still open:**
- **Nothing has run on hardware**: protocol v2 between the real Pi and the real ESP32, the GPRS
  and HTTPS/AWS paths, and the bench HIL checks (backend down → 60 s → DLQ → 5 attempts →
  archive, a live alert during a replay). See plan section 6.
- **Archived criticals are invisible to the backend.** After 5 failed attempts (about 80 s
  offline on the SIM800L) a critical stays only in the Pi's `archive` table. Retrying archived
  rows and a "truck lost connection" alert were left out of scope on purpose.
- **Reports** (the ER diagram's `Report`) don't count replayed alerts in any way, because there
  are no reports yet.
- The old device endpoints (`POST /api/alerts`, `POST /api/routes/{id}/status`, the device-key
  resolve) still accept writes without a `delivery` tag, until the later cleanup (contracts C2.4).

## Repository state and structure

Three parts of the planned architecture exist as code so far: the training notebook, the
`cv-argus` edge module, and now the `backend-argus` cloud backend (all below). The frontend
(`ui-argus`, below) exists too, and **every screen now calls the real backend** — Fleet,
Drivers, Routes, Live operations, and Alert triage all fetch real data (session storage,
route guarding, and login were the first pieces wired; the rest followed in the same later
pass), plus two new screens, Access (root_admin/admin user management) and Profile
(self-service account edit), that didn't exist as fixture-era mockups at all. `src/it-argus/`
(below) is a fourth, newer piece: Playwright integration tests exercising `ui-argus` and
`backend-argus` together — login, users, trucks, drivers, Live operations and, since the final
tweaks, incidents, closing with a note, stopping a route and position labels (29 specs).
`src/simulator-argus/` (below) is a fifth: a fleet simulator that provisions its
own demo trucks/routes against a real `backend-argus` and continuously POSTs the same combined
report a real ESP32 sends (`POST /api/devices/me/report`, device-API-key authenticated, per
truck), so `ui-argus`
has moving trucks and fresh alerts to demo against without real ESP32/Pi hardware. OSRM,
described in "Planned end-to-end system architecture", doesn't exist in this repo yet.
`src/esp32-argus` is a sixth: real Arduino firmware now exists there too (GPS, SIM800L mobile
data, WiFi, Bluetooth protocol v2 with `cv-argus`, a priority sender task, the report to
`backend-argus`) — see that module's own section below and its README.md. The pre-final-tweaks
firmware was compiled, flashed and bench-tested over WiFi; the final-tweaks firmware has only
run as host tests (`test/host/`, g++). See `docs/roadmap.md` for the full, up-to-date gap list across every module
(including what's built but not yet merged into `main`) — read that before assuming something
is missing or done.

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
  model inference → an output sink), not just the model-loading piece. **Deploys one pipeline:
  the tiny YOLO "parts" detector + tracker + rules** (BlazeFace face box → square gray crop →
  notebook 12's 46k-parameter `tiny_yolo_w1`, int8 TFLite by default or float `.keras` → IoU
  tracker → timed rules giving a none/low/medium/critical severity with a reason, mapped onto
  the binary `Not Drowsy`/`Drowsy` contract the ESP32 reads). Since the final tweaks: no face
  for 5 s gives one CRITICAL (no low/medium steps); one alert per incident (a MEDIUM, then at
  most one CRITICAL upgrade) and one driver-OK message on recovery; no 60 s heartbeat; it locks
  onto the largest face and ignores bystanders (switching after 5 s); and an inference filter
  drops head-sized eye boxes. This replaced the CNN+LSTM
  ("fused") pipeline on 2026-10-18, by the user's decision for a simpler, explainable system;
  that pipeline lives whole on branch `archive/cnn-lstm-fused`, and its model files are no
  longer downloaded. It logs one line per frame (severity, eye/mouth/head states, inference
  time) and can write a per-frame JSONL metrics log (`OUTPUTS=metrics`,
  `scripts/summarize_metrics.py`) for the report. If the int8 model can't load or run on the
  device (seen on a Pi 5), it falls back to the float `.keras` automatically. Built as a
  threaded, queue-connected `Stage`/`Pipeline` abstraction (see its `CLAUDE.md`'s "`pipeline/` —
  done") so a future model family could share the same camera-loop/threading/shutdown plumbing
  without reimplementing it; also has a demo mode (a browser-viewable live video stream with
  the drowsiness classification overlaid, see that file's "Demo" section) for actually watching
  it work on a laptop or the Pi (`scripts/demo.sh`). Docker-first (own `Dockerfile`/`docker-compose.yml`, plus a
  `docker-compose.pi.yml` overlay for the Pi's CSI camera). Has both a `README.md` (practical
  "how do I run this" — quick start, the demo, a config-variable table, troubleshooting) and a
  `CLAUDE.md` (architecture, why things are built the way they are, notebook-fidelity
  requirements); read the `CLAUDE.md` before *changing* anything in this module, not just
  running it — it has the details (exact model input/output shapes, why the model classes must
  be ported verbatim, etc.) that would otherwise need re-deriving from the notebook each session.
- `src/backend-argus/` — the cloud backend: **FastAPI + MongoDB (via Beanie)**, covering
  exactly six of the ER diagram's nine entities — **User, Truck, Driver, Route, Status_Route,
  Alert** — plus `/api/auth/login` and a self-service `/api/auth/me`. `Report`, `Device`,
  `Geofence` are in the ER diagram but deliberately out of scope (no consumer/committed endpoint
  for them yet). Fixes a handful of ER-diagram typos/inconsistencies (`blod_type`→`blood_type`,
  `reviwed_by_operator`→`reviewed_by_operator`, `Id_Route`→`id_route`, `updated_at` everywhere)
  — this module's `CLAUDE.md` is now the authoritative field-name reference, not the diagram.
  Password/device-key hashing via `bcrypt` directly and JWT via `PyJWT` (not `passlib`/
  `python-jose` — both unmaintained); a per-truck device API key (not a user login) authenticates
  the ESP32's writes to `/api/alerts`/`/api/routes/:id/status`, since cv-argus itself never calls
  this backend directly (alerts flow cv-argus → SQLite → Bluetooth → ESP32 → HTTP → here, per
  `cv-argus`'s own `CLAUDE.md`). Coordinates store as MongoDB GeoJSON internally but serialize as
  `{lat, lon}` at the API boundary, matching `ui-argus/src/types.ts` exactly. A `SEED_DEMO_DATA`
  env var idempotently seeds a demo fleet (admin/operator + guardian accounts, trucks, drivers,
  routes) on startup — safe to leave on in local dev, never in a real deployment. Has a
  `README.md` (quick start, Docker, demo seeding, config vars, verification checklist) and a
  `CLAUDE.md` (full design rationale, the four-role RBAC table, known gaps — e.g. no
  `User`↔`Driver`/`Truck` link yet, so `truck_driver`-role "own-data-only" scoping isn't real yet
  either); read that `CLAUDE.md` before changing anything here. A root-level `docker-compose.yml`
  (alongside each module's own) wires this backend + MongoDB + `ui-argus` together for local
  integration testing; no OSRM service in it yet, per the "still deferred" note above.
- `src/it-argus/` — **Playwright** browser tests driving the real `ui-argus` dev server against
  the real `backend-argus` + MongoDB together, the seam neither module's own unit tests exercise
  (the backend's mock the browser away, the frontend's mock `fetch` away). Its own fully
  self-contained `docker-compose.yml` (own Mongo, own backend/UI builds, no host ports, real
  healthchecks) rather than an overlay on the root one — see that module's `CLAUDE.md` for why.
  Covers the login flow (root_admin bootstrap login, wrong-password error, an
  unauthenticated deep link redirecting to `/login` and back after signing in, sign-out), users,
  trucks, drivers, Live operations, and since the final tweaks incidents (grouping, close with a
  note), stopping a route (reports rejected afterwards) and position labels. The UI halves of the
  final-tweaks specs run with `IT_FINAL_TWEAKS_UI=1`. Meant to grow alongside future
  backend-connected `ui-argus` screens, not a one-off.
- `src/simulator-argus/` — a fleet simulator turning `docs/roadmap.md`'s previously-undone
  "software-only simulator" idea (section 7) into real code: N virtual trucks that provision
  their own `SIM-`-prefixed drivers/trucks/routes via the admin API (not `SEED_DEMO_DATA`), mint
  a real per-truck device API key each (`POST /api/trucks/{id}/rotate-key`), and continuously
  POST combined reports (`POST /api/devices/me/report`: GPS points every 5 s, one alert per
  incident, then a driver OK) against `backend-argus` using that key — exactly the
  auth path a real ESP32 would use, scoped server-side to each truck's own route/alert writes.
  Three per-truck profiles (`normal`, `drowsy_escalation`, `no_face`; `no_face` replaced the
  retired `panic` profile, and an old `panic=` weight maps to it) are assigned by a weighted
  random draw (`SIMULATOR_SCENARIO_WEIGHTS`) rather than a fixed rotation, giving a demo run a
  coherent story per truck while which-truck-gets-which stays randomized; route duration
  (`SIMULATOR_ROUTE_DURATION_MINUTES`, minutes to hours) and event timing are both parametrized
  rather than hardcoded. Movement optionally follows a real OSRM-computed road route instead of a
  straight line (`SIMULATOR_USE_OSRM`, on by default — needs a one-time Mexico OSM extract
  setup to actually take effect, falls back to a straight line if that hasn't been run yet or
  OSRM is otherwise unreachable) — a private, simulator-only use of
  OSRM for realistic demo tracks, distinct from the backend/frontend's own route+ETA feature
  described above, which remains deferred. Has its own `docker-compose.yml` (a full,
  self-contained mini-stack — Mongo + backend + UI + simulator + an opt-in OSRM service,
  published on alternate ports for a one-command demo) plus a `profiles: ["simulator"]`-gated
  service in the root `docker-compose.yml` for layering onto an already-running dev stack. Has a
  `README.md` (quick start, config table, what to expect, OSRM setup) and a `CLAUDE.md` (why
  device-key auth, why self-provisioned data, the scenario/movement-model/OSRM design); read that
  `CLAUDE.md` before changing anything here.
- `src/esp32-argus/` — the ESP32 "Message Sender Orchestrator" firmware, an Arduino IDE
  multi-tab sketch (`src/esp32_argus/esp32_argus.ino` + per-concern `.h`/`.cpp` pairs: GPS
  with an NVS last-known position, the SIM800L modem, WiFi, the backend report client, the
  Bluetooth v2 client pulling from `cv-argus`, the sender task and its host-tested policy, the
  drowsy+grip fusion state machine ported 1:1 from `cv-argus`'s `fusion_contract.py` (unused
  until a grip sensor exists), the grip sensor). Mobile data over the SIM800L's GPRS (the
  modem's own HTTPS stack) is the first-choice transport to `backend-argus`, with WiFi (e.g. a
  phone hotspot) as the fallback — see its `README.md`'s network-path decision, including
  why the AWS deployment needs `https://` (CloudFront answers 403 to plain HTTP) and why the
  SIM800L's TLS 1.0 still reaches the default `*.cloudfront.net` domain. The panic button and
  its SMS were removed in the final tweaks. Since then the device's only backend write is
  `POST /api/devices/me/report`. See its own `README.md` for the full
  protocol/field-mapping contracts this implements. `config.h` holds only wiring/timing and the
  Pi's Bluetooth MAC: the backend URL, device key, WiFi network and mobile-data APN all come from
  the Pi over Bluetooth (`CONFIG`, set in cv-argus's `argus-device.env` + `.env`), and the route
  from `GET /api/devices/me/active-route` (and from each report's answer), so one firmware
  image fits every truck. The pre-final-tweaks firmware compiled, was flashed to a real ESP32,
  and `test/hil/` passed over WiFi against a bench backend (see `src/esp32_argus/CLAUDE.md`).
  The final-tweaks firmware has passed its host tests only; it hasn't been compiled here (no
  Arduino toolchain; `test/compile.sh` is for the user) or flashed. The GPRS path and the
  AWS/HTTPS path haven't run on hardware yet.
- `src/infra-argus/` — **AWS CDK (TypeScript) demo deployment, cheapest possible, pay-per-use**:
  `backend-argus` as its **unchanged** Docker image on Lambda (via the Lambda Web Adapter, a
  wrapper image in `docker/backend-lambda/`, no backend code changes for Lambda) behind a
  Function URL; `ui-argus` on S3 + one CloudFront distribution that also routes `/api/*` to the
  Lambda (same origin, no CORS); MongoDB Atlas M0 (free, external); and the fleet simulator +
  OSRM as an on-demand **Fargate Spot** task that root_admin turns on/off from Live operations
  (`/api/simulator`, root_admin only). **Hard cost guardrails are a non-negotiable requirement
  here, not tuning**: every simulator session is a fixed 10 minutes, at most one at a time, 12
  sessions/month, a 1-minute reaper Lambda, a $3 AWS Budget whose action denies further starts,
  an invocation kill switch, and synth-time tests that fail on any always-on paid resource (NAT
  Gateway, ALB, RDS/DocumentDB, EC2, ...). Estimated bill ≈ $0.30/month. See its `README.md`
  (the R1–R10 table + cost table) and `CLAUDE.md` before changing anything there. Written and
  tested (synth assertions, Lambda unit tests, backend + UI tests); **not yet deployed to a
  real AWS account**.
- `docs/argus-descripción-proyecto.pdf` — project description/proposal.
- `docs/criteria/` — academic thesis/grading-criteria documents (this is a school "trabajo de
  grado" project); `Formato_Proyecto_Modular V2.docx` is the report template being filled in.
- `docs/designs/semantic-design*` — draw.io system architecture diagram; source of truth for
  the planned end-to-end architecture summarized above (`semantic-design` is the raw XML,
  `semantic-design.drawio.png` a rendered export — re-render after editing the XML).
- `docs/designs/cnn-lstm-mediapipe-pipeline.svg` — model-level diagram of what `src/cv-argus`
  deployed until 2026-10-18 (now on branch `archive/cnn-lstm-fused`; the current parts pipeline
  has no diagram yet), scoped to the model itself rather than the full edge/cloud system:
  both MediaPipe stages (Face Detector crop, then FaceLandmarker run on that crop) feeding a
  frozen-CNN-embedding + `GeometricRatioFeatureLayer` fusion into the LSTM classifier's
  zero-pre-padded 100-frame window, thresholded at `p(Drowsy) >= t*` (`t* = 0.57`) rather than
  `argmax`. Source of truth is `src/cv-argus/CLAUDE.md`'s "fused detector" section and
  `src/cv-argus/src/constants.py` (`FUSED_MODEL_EMBED_DIM`, `FUSED_MODEL_NUM_GEO_FEATURES`,
  `FUSED_MODEL_MAX_TIMESTEPS`, `FUSED_MODEL_THRESHOLD`) — re-derive dimensions/thresholds from
  there, not from this diagram, if either changes. The `.svg` is the source of truth for this
  diagram; `cnn-lstm-mediapipe-pipeline.png` (flat export), `.drawio` (same layout/colors/curved
  connectors, editable in diagrams.net), and `.mmd` (a Mermaid port, lower style fidelity but
  renders anywhere Mermaid does, GitHub included) are generated from it and can drift out of
  sync with the `.svg` if only one is edited by hand later.
- `docs/references/` — background research papers on drowsiness/microsleep detection that
  inform feature and model choices.

**Current deployment status, stated plainly since it's easy to lose track of amid the history
below: `src/cv-argus` deploys one pipeline — the tiny YOLO "parts" detector + tracker +
deterministic rules** (notebook 12's `tiny_yolo_w1`, int8 by default; see `src/cv-argus/
CLAUDE.md`'s "Current status").
- Deployed model: the 2026-10-20 23:35 run (int8 on notebook 12's test set: mAP@0.5 0.756,
  eye-state accuracy 0.920, head_down AP@0.5 0.743, head accuracy 0.994). The 2026-10-18 model it
  replaced scored mAP@0.5 0.63, eye-state 0.95 and head_down AP about 0.25.
- Measured in the container on a desktop CPU under the 4-core Pi limit: 0.7 ms per face at int8
  and about 15 ms end to end.
- Not yet run on a Pi 5, on real cabin footage, or on unseen drivers.

It replaced the CNN+LSTM below on 2026-10-18, by the user's decision for a simpler, explainable
system. That pipeline is preserved on branch `archive/cnn-lstm-fused`.

**Before that, `src/cv-argus` deployed the binary frozen-CNN-embedding +
geometric-feature-fusion + LSTM classifier from `11_cnn_lstm_training_drive_pull.ipynb`** (a
frozen convolutional embedding + a FaceLandmarker geometric-feature subset, fused per frame and
classified over a rolling window as `Not Drowsy` vs. `Drowsy`). That wasn't the first thing
deployed either; the record of what was tried and rejected along the way is worth keeping, because each
step is a real, measured reason the next one won:

- **First deployed: the single-frame CNN** (`07_cnn_training.ipynb`'s model, `36.67%` accuracy /
  `0.3614` macro-F1 single-fold; `35.93% ± 9.07%` across a 3-fold subject-CV diagnostic, against
  a 24-subject pool) — chosen over the architecturally-intended LSTM (windowed geometric
  features only) mainly because it was the cheapest real result available at the time, not
  because it beat the LSTM on numbers; the LSTM itself was never deployed.
- **The CNN+LSTM was tried next and did not beat the single-frame CNN, twice.** An early
  24-subject smoke test (46.79% accuracy / 0.4090 macro-F1, MobileNetV2 frozen) looked
  promising but didn't hold up on the full 54-subject pool (33.68% / 0.3340, below both Dense NN
  and the deployed CNN). A later rerun with geometric-feature fusion and stronger
  regularization also stayed just under the CNN's number (35.04% / 0.3296, from-scratch
  backbone; MobileNetV2 overfit even worse and was disabled). All of this was still the
  pre-binary-migration 3-class record — see `notebook/CLAUDE.md`'s "What we found" for the full
  numbers.
- **The result that actually changed the decision: a genuine binary rerun, not a 3-class one.**
  `11_cnn_lstm_training_drive_pull.ipynb`'s frozen-CNN-embedding + LSTM variant, rerun after
  fixing an LR bug (see `notebook/CLAUDE.md`'s "`11_…`" section) with a retrained `07` checkpoint
  and active minority-class window rebalancing, reached **84.24% test accuracy, 0.8375
  macro-F1** (Not Drowsy R 0.94, Drowsy R 0.73; 84.38%/0.8379 at the chosen safety threshold
  `t*=0.57`) on 10 held-out subjects — roughly double `07`'s own fresh binary single-frame
  result (59.64% accuracy, 0.5273 macro-F1) on the same subjects, and by a wide margin the best
  number this project has produced. This is what made deploying a CNN+LSTM shape worth it: not
  the architecture in the abstract, but this specific, much stronger measured result.

**That architecture was what `src/cv-argus` deployed until 2026-10-18**, replacing both the single-frame CNN
and the never-deployed windowed-geometric-only LSTM (removed, not merely deprioritized — see
`src/cv-argus/CLAUDE.md`'s "Current status"). `src/cv-argus/CLAUDE.md`'s "fused detector"
section has the deployment-side mechanics; `notebook/CLAUDE.md`'s "`11_
cnn_lstm_training_drive_pull.ipynb`'s fixed rerun" section has the full numbers. Real caveats
that don't go away just because it's now deployed: it's a single `StratifiedGroupKFold` fold
with no subject-CV diagnostic yet (this project's own 3-fold CV elsewhere has shown ±9-point
macro-F1 swings at similar subject counts); the test set's minority-class windows are
overlap-tiled from the same clips, so the effective independent sample size is smaller than the
raw window count suggests; a real, still-open risk is unresolved — whether the CNN checkpoint
`src/cv-argus` downloads as the frozen embedding backbone (`CNN_MODEL_DRIVE_FILE_ID`) is
actually the same weights `11` trained its embeddings against, since a mismatch would silently
degrade accuracy rather than crash; and while it has now been **run end to end against the real
trained checkpoints on a desktop CPU** (webcam → `docker compose up`, classifications tracking
correctly), it has **not** run on real Pi hardware. That first real run also exposed a large
inference-speed bug — the fused LSTM was being called eagerly, ~600 ms/frame (a `tf.function`
wrapper fixed it to ~10 ms; see `src/cv-argus/CLAUDE.md`'s fused-detector section) — so treat
any earlier "it runs" claim as pre-profiling. It's still the heaviest per-frame pipeline in the
project (two MediaPipe tasks and two Keras models per sampled frame). That first run also
surfaced a correctness gap, not just a speed one: nothing capped the capture rate, so the
LSTM's 100-frame window was spanning ~4s of real time instead of the ~20s it was trained on
(`src/dataset`'s `SAMPLING_FPS = 5`). `src/cv-argus` now samples at 5 fps **at the source**
(`SAMPLE_FPS`, decimated before any MediaPipe/CNN/LSTM work runs) so the deployed window
matches training again. The container's `docker-compose.yml` also now defaults to a **simulated
Raspberry Pi 5 resource envelope** — a cgroup `cpuset`/`cpus` limit to 4 cores plus an 8 GB
memory cap, not real ARM hardware — under which the whole pipeline (two MediaPipe tasks, the
CNN embed, the LSTM) measured ~54% of one core and ~344 MB resident, ~80 ms end-to-end per
sampled frame, zero dropped frames. That's a real, measured number under a faithful *simulated*
ceiling, not a substitute for running on an actual Pi 5 board — see `src/cv-argus/CLAUDE.md`'s
"Current status" for the full blocker list before treating this as a validated production
result rather than the best real result the project has produced so far.

## Working in this repo

- **The drowsiness class scheme is binary: `Not Drowsy` vs. `Drowsy`** (`drowsy_vs_not` —
  `Not Drowsy` = old Alert + Low Vigilant merged, `Drowsy` = old Drowsy). This is the settled
  decision, not one option among several. The **code** side is complete: all ten notebooks
  (`01`–`10`, dataset creation + training + export) are migrated — `CLASS_NAMES =
  ["Not Drowsy", "Drowsy"]`, `NUM_CLASSES = 2`, `== {1, 2}` fold guards — and the
  dataset-creation ones read a new `dataset/raw_videos_binary/` tree. `01`–`08` still haven't
  been rerun under this scheme (they still load their old 3-class Drive artifacts, so results and
  framing for those in `notebook/CLAUDE.md` are the pre-migration record), but the `06`→`09`→`10`/
  `11` chain feeding `src/cv-argus`'s previously deployed model has, and `src/cv-argus` still
  emits binary class names (`Not Drowsy`/`Drowsy`) to match — see "Current deployment status" above. See
  `notebook/CLAUDE.md`'s "Binary migration" for the authoritative per-notebook status.
- For **training** logic (`03`/`04`/`05`/`07`/`08`/`10`) the notebooks are the source of truth;
  edit the corresponding cell(s). For **dataset creation** (`01`/`02`/`06`/`09`) the source of
  truth is now `src/dataset/` — edit `argus_dataset/config.py` + the relevant module there, and
  mirror the constant into the matching notebook (kept as Colab reference).
- **`07` and `11_cnn_lstm_training_drive_pull` now choose a `Drowsy` decision threshold** (not
  `argmax`) on the validation set after training and write it to `<checkpoint>.keras.threshold.json`
  next to the model. `src/cv-argus` now deploys `11`'s fused model and thresholds `p(Drowsy)`
  the same way, via a checked-in `FUSED_MODEL_THRESHOLD` constant rather than reading the JSON
  file directly (see `src/cv-argus/CLAUDE.md`'s "Model download strategy" for why it's checked
  in). Selection is safety-first: hit a `Drowsy` recall floor, then maximise precision.
  `11_drive_pull` also had a learning-rate bug
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
  `08_deployment_export_lstm.ipynb`, `src/dataset/tests/reference_geometric_layer.py` (moved
  from `src/cv-argus/src/model/layers.py` when cv-argus dropped the CNN+LSTM), and now
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
  against the reference layer (`reference_geometric_layer.py`) to `atol=1e-4`.
- `MAX_TIMESTEPS` (the LSTM's fixed padded-window length, currently 30 — it was 60 until
  `01`/`02`'s `sampling_fps` dropped from 10 to 5, making a 6s window 30 frames) is an LSTM-only concept.
  It has no bearing on RandomForest, the Dense NN, or the CNN — those three consume single-frame
  or single-image rows with no timestep dimension at all, from a dataset (`frame_features.csv` /
  `face_crops_index.csv`) that was never windowed in the first place.
