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

Two of the planned pieces exist as code so far; the rest — backend, frontend, ESP32 firmware —
is design work not yet implemented (see `docs/designs/semantic-design*` for the full planned
architecture):

- **`notebook/`** — the ML pipeline. Raw drowsiness-labeled video → four candidate model
  families (LSTM, RandomForest, Dense NN, a face-crop CNN) → a deployable artifact. See
  [`notebook/CLAUDE.md`](notebook/CLAUDE.md) for what's actually been run and what it found.
- **`src/cv-argus/`** — the Raspberry Pi 5 edge module: camera → MediaPipe → model inference →
  a drowsiness classification, running live. **Currently focused on and deploying the CNN
  face-crop model by default** — a practical, current decision (the LSTM remains the
  architecturally-intended long-term model, and is still kept, fully functional, and selectable).
  See [`src/cv-argus/CLAUDE.md`](src/cv-argus/CLAUDE.md) for the full picture, including the
  honest caveat that the CNN's own reported accuracy isn't yet a validated number.

## Repository layout

```
notebook/       ML pipeline (Colab notebooks; Drive-backed, no local dataset in this repo)
src/cv-argus/   Raspberry Pi 5 edge module (Docker-first)
docs/           Project proposal, academic grading criteria, architecture diagrams, references
```

Each of `notebook/` and `src/cv-argus/` has its own `CLAUDE.md` with the real technical depth
(exact feature/model shapes, why certain classes must be byte-identical across files, container
conventions, what's been measured vs. what's still aspirational) — read those before making
changes in either directory; this file stays at the overview level on purpose.

## Running the edge pipeline

```sh
cd src/cv-argus
docker compose up --build
```

Builds and deploys the CNN pipeline by default, reading from a webcam (`CAMERA_SOURCE`) or a
video file. To actually *watch* it work — a live feed with the drowsiness classification
overlaid, viewable from any device's browser on the network — plus the separate procedure for
running it on the Pi 5's own CSI camera, a config-variable reference, and troubleshooting, see
[`src/cv-argus/README.md`](src/cv-argus/README.md).

## Documentation

- [`CLAUDE.md`](CLAUDE.md) — the full technical/architectural contract for this repo: system
  design, notebook pipeline internals, model results and current status, working conventions.
- [`notebook/CLAUDE.md`](notebook/CLAUDE.md) — the ML pipeline's empirical record: what's been
  run, what the results were, and why the CNN is the current focus.
- [`src/cv-argus/README.md`](src/cv-argus/README.md) — how to actually run the edge module: quick
  start, the demo, configuration, troubleshooting.
- [`src/cv-argus/CLAUDE.md`](src/cv-argus/CLAUDE.md) — the edge module's architecture, container
  conventions, and notebook-fidelity requirements.
- `docs/argus-descripción-proyecto.pdf` — project description/proposal.
- `docs/designs/semantic-design*` — the planned end-to-end system architecture diagram.
