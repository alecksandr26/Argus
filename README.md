# Argus

Argus is a Driver Monitoring System (DMS) for truck drivers in Mexico. Instead of full
automation (Level 5), it aims at **"Human Augmentation"**: a technological safety barrier
against fatigue, drowsiness, and health emergencies — a preventive layer on top of a human
driver, not a replacement for one.

## Why not full autonomy

Full autonomy is judged infeasible in Mexico near-term, for reasons specific to this context
rather than a generic "the tech isn't ready" claim:

- **Cargo-theft/security risk** — a stopped autonomous truck, with no driver aboard to react, is
  an easy target.
- **Infrastructure** — inconsistent road quality/lane markings and no widespread low-latency 5G
  make Level-4/5 driving assumptions unreliable.
- **Cost** — a Level-4-capable truck runs roughly $450k USD versus ~$180k for a conventional one.

Argus's answer is a driver monitoring layer, not a replacement driver: catch drowsiness and
health emergencies early enough to matter, while keeping a human in control.

## Current status

This is also an academic *titulación* ("trabajo de grado") project for an Ingeniería en
Computación program, and its architecture is deliberately shaped to cover three required areas:
**Arquitectura y Programación** (justified language/data-structure/methodology choices),
**Sistemas Inteligentes** (a justified ML/CV pipeline), and **Sistemas Distribuidos** (a
genuinely decentralized edge/cloud system, not a UI over a monolith).

Four of the planned pieces exist as code now, and the cloud half (backend + UI) is wired
together end to end; the ESP32 firmware is the one piece that's still pure design (see
`docs/designs/semantic-design*` for the full planned architecture, and `docs/roadmap.md` for the
up-to-date gap list across every module):

- **`notebook/`** — the ML pipeline. Raw drowsiness-labeled video → four candidate model
  families (LSTM, RandomForest, Dense NN, a face-crop CNN / CNN+LSTM) → a deployable artifact.
  See [`notebook/CLAUDE.md`](notebook/CLAUDE.md) for what's actually been run and what it found.
- **`src/cv-argus/`** — the Raspberry Pi 5 edge module: camera → MediaPipe → model inference →
  a drowsiness classification, running live, plus the alert pipeline (`alerts/`/`buffer/`/
  `orchestrator/`/`sender/`) that queues and relays classifications toward the cloud backend.
  See [`src/cv-argus/CLAUDE.md`](src/cv-argus/CLAUDE.md) for the deployed model architecture and
  its measured results.
- **`src/backend-argus/`** — the cloud backend: FastAPI + MongoDB (Beanie), covering User,
  Truck, Driver, Route, Status_Route, Alert, plus auth. **Four roles**: `root_admin` (full
  control, including user management), `admin` (operations — schedules routes, manages the
  fleet/driver roster, with a narrow, server-enforced exception to manage `guardian` accounts
  only), `guardian` (read-only monitoring + alert review), `truck_driver`. A `SEED_DEMO_DATA` env
  var can idempotently seed a demo fleet on startup. See
  [`src/backend-argus/README.md`](src/backend-argus/README.md) for how to run it and
  [`src/backend-argus/CLAUDE.md`](src/backend-argus/CLAUDE.md) for the full design rationale.
- **`src/ui-argus/`** — the web frontend: a React + TypeScript (Vite) SPA serving all four
  roles. **Every screen now calls the real backend** — Fleet, Drivers, Routes & trips, Live
  operations, and Alert triage all fetch real data, plus two new screens: **Access** (root_admin/
  admin user management) and **Profile** (self-service account edit for any role). Role-based
  nav gating and read-only views for roles without write access are real. See
  [`src/ui-argus/README.md`](src/ui-argus/README.md) for how to run it and
  [`src/ui-argus/CLAUDE.md`](src/ui-argus/CLAUDE.md) for why it's built this way.

**Still design-only**: the ESP32 firmware (the device that bridges `cv-argus`'s Bluetooth alert
buffer to `backend-argus` over HTTP) — see `docs/roadmap.md`'s ESP32 section for the exact
contracts it needs to implement against, already specified and tested on both sides.

## Repository layout

```
notebook/            ML pipeline (Colab notebooks; Drive-backed, no local dataset in this repo)
src/dataset/         Local dataset-creation pipeline (CPU-parallel reimplementation of 01/02/06/09)
src/cv-argus/        Raspberry Pi 5 edge module — model inference + alert pipeline (Docker-first)
src/backend-argus/   Cloud backend — FastAPI + MongoDB (Docker-first)
src/ui-argus/        Web frontend — React + TypeScript SPA (Vite, Docker-first)
src/it-argus/        Playwright integration tests driving ui-argus + backend-argus together
src/esp32-argus/     Not built yet — README documents the contracts to implement against
docs/                Project proposal, academic grading criteria, architecture diagrams, references
docker-compose.yml   Whole cloud stack: backend-argus + MongoDB + ui-argus together
```

Each `src/*` module has its own `CLAUDE.md` with the real technical depth (exact feature/model
shapes, why certain classes must be byte-identical across files, container conventions, what's
been measured vs. what's still aspirational) — read those before making changes in a given
directory; this file stays at the overview level on purpose.

## Running the whole cloud stack (backend + Mongo + web frontend)

```sh
cp .env.example .env   # optional — every var has a checked-in default in docker-compose.yml
docker compose up --build   # from this repo root
```

Boots `backend-argus` (http://localhost:8000/docs) + MongoDB + `ui-argus` (http://localhost:5173)
together — the actual integration stack, now that both modules exist and are wired to each
other. **Note**: this root-level `.env` is separate from `src/backend-argus/.env` — this compose
file doesn't read that one at all, only its own directory's `.env` (or `VAR=value docker compose
up`/a shell `export`). Log in with the bootstrapped root_admin (`admin@argus.dev` /
`changeme123` by default), or set `SEED_DEMO_DATA=true` in this `.env` for a full demo fleet
(admin/operator + guardian accounts, several trucks/drivers/routes) seeded on startup — see
`src/backend-argus/README.md`'s "Seeding demo data" for the full picture. See
`src/backend-argus/CLAUDE.md` and `src/ui-argus/CLAUDE.md`/`INTEGRATION.md` for what's actually
implemented and wired versus still open.

## Running the edge pipeline

```sh
cd src/cv-argus
docker compose up --build   # only needed the first time, or after a Dockerfile/dep change —
                            # plain `docker compose up` after that reuses the built image
```

Builds and deploys the CNN pipeline by default, reading from a webcam (`CAMERA_SOURCE`) or a
video file. To actually *watch* it work — a live feed with the drowsiness classification
overlaid, viewable from any device's browser on the network — plus the separate procedure for
running it on the Pi 5's own CSI camera, a config-variable reference, and troubleshooting, see
[`src/cv-argus/README.md`](src/cv-argus/README.md).

## Running the web frontend

```sh
cd src/ui-argus
cp .env.example .env
docker compose up --build   # only needed the first time, or after a Dockerfile/dep change
```

Opens a Vite dev server on http://localhost:5173 with hot reload (source is bind-mounted). For
the local-Node path (no Docker), the full command table, the production nginx image, and
troubleshooting, see [`src/ui-argus/README.md`](src/ui-argus/README.md). This runs the frontend
alone, against whatever `VITE_API_BASE_URL` points at — see "Running the whole cloud stack"
above to run it together with a real `backend-argus` + MongoDB.

## Documentation

- [`CLAUDE.md`](CLAUDE.md) — the full technical/architectural contract for this repo: system
  design, notebook pipeline internals, model results and current status, working conventions.
- [`notebook/CLAUDE.md`](notebook/CLAUDE.md) — the ML pipeline's empirical record: what's been
  run, what the results were, and why the CNN is the current focus.
- [`src/cv-argus/README.md`](src/cv-argus/README.md) — how to actually run the edge module: quick
  start, the demo, configuration, troubleshooting.
- [`src/cv-argus/CLAUDE.md`](src/cv-argus/CLAUDE.md) — the edge module's architecture, container
  conventions, and notebook-fidelity requirements.
- [`src/backend-argus/README.md`](src/backend-argus/README.md) — how to run the cloud backend:
  quick start, tests, demo-data seeding, config vars, a verification checklist.
- [`src/backend-argus/CLAUDE.md`](src/backend-argus/CLAUDE.md) — the backend's design rationale:
  auth, the four-role RBAC model, geo storage, ER-diagram field-name corrections, known gaps.
- [`src/ui-argus/README.md`](src/ui-argus/README.md) — how to run the web frontend: Docker and
  local-Node quick starts, command reference, production image, configuration, troubleshooting.
- [`src/ui-argus/CLAUDE.md`](src/ui-argus/CLAUDE.md) — the frontend's stack choices, Docker
  architecture, current status, and next steps.
- [`src/ui-argus/INTEGRATION.md`](src/ui-argus/INTEGRATION.md) — per-screen backend-wiring status
  and what's still genuinely open (OSRM, real-time push, `Alert.media_url`).
- `docs/roadmap.md` — the up-to-date, module-by-module gap list across the whole project.
- `docs/argus-descripción-proyecto.pdf` — project description/proposal.
- `docs/designs/semantic-design*` — the planned end-to-end system architecture diagram.
