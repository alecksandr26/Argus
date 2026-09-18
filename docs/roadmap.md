# Argus roadmap — what's missing, module by module

A living gap-analysis document: what exists, what's merged vs. sitting on a branch, and what's
genuinely not built anywhere. Grounded in `docs/` (the borrador project doc, the ER and
semantic-design diagrams, the grading-criteria PDF) cross-referenced against the real code
across `main` and the branches in flight as of this writing. Update this file as gaps close —
don't let it drift the way some of the per-module docs briefly did (see the "documentation
itself" section at the bottom for that story).

## Read this first: what's on `main` now

Both pieces this section used to describe as unmerged are now in `main` — `src/backend-argus`
and `src/cv-argus`'s `alerts/`/`buffer/`/`orchestrator/`/`sender/` modules (merged in commits
`6a3a855`, `645ef05`, `dea719c`; their feature branches are gone), plus a second wave of work
also since merged: a real-API/RBAC rebuild of `ui-argus` (Access/Profile screens, four roles),
and a `backend-argus` schema change unifying `Alert`/`Status_Route` onto one shared `Severity`
scale with a fused `source` (`fusion`/`panic_button`) field for grip-sensor input. Sections 1–4
below are corrected to describe this as `main`'s actual current state, not branches in flight.

Everything below distinguishes "missing from the project" (nobody has built it) from "missing
from what's built" (it's real code, just not everything it needs yet).

## Módulo 3 (Sistemas Distribuidos) grading reality check

`docs/criteria/criteriosaprobacion_0.pdf` requires **architecture**: real-time communication
between at least two devices, a justified client-server/peer-to-peer algorithm, justified
protocols (3.1–3.4). It does **not** require an automated test suite, E2E tests, or a simulation
deliverable — that bar (section 7 below) is a quality goal this project is choosing for itself,
not something the grading rubric asks for. Worth keeping in mind so the testing-framework
decision doesn't get overbuilt chasing a requirement that isn't actually there. The rubric does
warn that a plain FastAPI backend + HTTP clients "no cuenta por sí solo como sistema
distribuido" — the Pi↔ESP32 Bluetooth leg (section 2) is what actually satisfies this, and the
*polling algorithm's* failure-handling detail (what happens if Bluetooth drops mid-pull, how
`sent`/`unsent` is tracked) is explicitly the level of detail the rubric expects documented in
the report, not just "we used Bluetooth."

## 1. `cv-argus` (Raspberry Pi edge)

**Done, in `main`:** `model/` (fused CNN-embedding + geometric-feature + LSTM classifier, 84.24%
measured accuracy / 0.8375 macro-F1, one held-out split — see its own `CLAUDE.md` for the full
caveats), `pipeline/` (threaded Stage/Pipeline abstraction, MediaPipe stages, camera sources),
and the alert pipeline — `alerts/` (Alert/AlertKind data model + serialization), `buffer/`
(WAL-mode SQLite queue, concurrency-tested), `orchestrator/` (debounce/cooldown decision loop +
heartbeat), `sender/` (a custom Bluetooth SPP protocol — see section 2 below — fully implemented
and unit-tested against a fake transport, `FakeTransport`).

`orchestrator/` also now has `fusion_contract.py` — a typed reference contract (enums, dataclasses,
a severity matrix, and stubbed debounce/escalation/recovery timing) for the ESP32's drowsy+grip
fusion decision, added alongside the severity-taxonomy work in sections 2–4. It's a contract
reference only, not wired into `main.py` or `Orchestrator` itself — `cv-argus`'s own decision loop
is unchanged and still camera-only.

**Missing:**
- Never run against real Raspberry Pi 5 hardware — only a desktop-CPU Docker container so far.
- Neither `docker-compose.yml` nor `docker-compose.pi.yml` passes through a real Bluetooth
  adapter (`SENDER_TRANSPORT` defaults to `none` specifically because setting it to `bluetooth`
  without real device passthrough crashes at startup).
- CSI camera passthrough (`docker-compose.pi.yml`) is a best-effort device list, not verified
  against real hardware.
- The CNN checkpoint the fused model's embedding backbone loads hasn't been hash-verified as the
  exact same training run `11_cnn_lstm_training_drive_pull.ipynb` used — a real, open risk of a
  silent accuracy bug, not a crash.
- No subject-level cross-validation on the deployed model yet (one split only).
- Debounce/cooldown/heartbeat constants are reasoned defaults, not tuned against a real
  multi-hour recorded drive.
- Grip sensor / panic button / CAN bus / alarm speaker / geolocation: **not this module's job at
  all** — see section 2. `cv-argus`'s own `CLAUDE.md` says so explicitly. The grip+drowsy fusion
  *algorithm* is now fully specified (`src/esp32-argus/README.md` section 5,
  `fusion_contract.py` above) even though the hardware/firmware itself still doesn't exist.

## 2. ESP32 firmware — missing entirely

No code exists anywhere in this repo. This is the single biggest concrete gap, and it's the
device that owns everything actuation-/safety-critical per the root `CLAUDE.md`: the alarm
speaker, the CAN bus/AEB actuator, the panic button, the geolocation (GPS) module, and — per the
`semantic-design.drawio.xml` diagram — the steering-wheel grip sensor. None of those five have
any code anywhere, on either the Pi or ESP32 side; they're drawn as distinct boxes in the
diagram and explicitly out of `cv-argus`'s scope.

**What it needs to implement, against contracts that already exist and are already tested:**

1. **Bluetooth SPP client**, speaking the exact protocol `cv-argus/src/sender/protocol.py`
   already implements server-side (see `src/esp32-argus/README.md` for the full grammar).
2. **HTTP relay to `backend-argus`**, using a per-truck device API key (`X-Device-Api-Key`
   header) against `POST /api/alerts` and `POST /api/routes/{id}/status` — this auth scheme was
   built specifically anticipating this caller; see `src/backend-argus/CLAUDE.md`'s "Device
   (ESP32) auth" section.
3. **Attaching real GPS coordinates** to every record it relays — `cv-argus` never populates
   `geolocation` itself (no GPS on the Pi in this design); the ESP32 is expected to fill
   `coordinates`/`current_coordinates` before the HTTP call, not after.
4. **The drowsy+grip fusion decision loop** — no longer an open design question:
   `src/esp32-argus/README.md` section 5 now specifies the full algorithm (severity matrix,
   debounce/escalation/recovery timing windows, how an escalation posts a new linked alert
   instead of mutating the original, how recovery sets `resolved_at`), with a typed reference
   implementation at `src/cv-argus/src/orchestrator/fusion_contract.py` to translate 1:1 into
   firmware. What's still missing is purely the hardware-interfacing side: the grip sensor's
   physical GPIO signal, and the panic button/CAN-bus-AEB-actuator/alarm-speaker
   GPIO/UART integration — genuinely new hardware work with no existing code to build on, but no
   longer an *algorithm* gap.

`src/esp32-argus/README.md` documents the exact protocol/contract/fusion details so this can
start without re-deriving them from `cv-argus`'s and `backend-argus`'s source.

## 3. `backend-argus`

**Done, in `main`**: FastAPI + MongoDB (Beanie) covering User, Truck, Driver, Route,
Status_Route, Alert + `/api/auth/login`. **RBAC is now four roles, not three** — `admin` was
added as a deliberate product decision: an operations role with write access on Truck/Driver/
Route, plus one narrow, server-enforced exception to otherwise-zero user-management access (it
may create/edit/deactivate `guardian`-role accounts only — see that module's `CLAUDE.md`'s "RBAC
— four roles" for exactly how it's scoped, including why non-guardian targets 404 rather than
403). Also new: self-service `GET`/`PUT /api/auth/me` so any role can edit their own email/name/
phone/password without needing `/api/users` access. A device-API-key auth path for the ESP32,
`GET /api/routes/active` for the live dashboard, and a `SEED_DEMO_DATA` env var that
idempotently seeds a demo fleet (admin/operator + guardian accounts, trucks, drivers, routes) on
startup — verified against a real Mongo container: no duplicates on repeat runs, all seeded
accounts actually log in. Verified: hermetic pytest tier passes, a full Docker stack (backend +
Mongo + `ui-argus`) has previously booted and round-tripped real HTTP calls.

**Also done:** the severity-taxonomy unification — `StatusRoute.vigilance` and
`Alert.severity_level` previously used two separately-named, mismatched 3-tier enums; both now
share one `Severity` enum (`critical`/`medium`/`low`). `Alert` gained `source`
(`fusion`/`panic_button`), `grip_status`, `related_alert_id` (escalation linking), and
`resolved_at` (recovery tracking) so a grip-sensor/panic-button-driven alert doesn't need to fake
CV scores — see `src/backend-argus/CLAUDE.md`'s "Coordination note" and "Why
`Status_Route.vigilance`" sections. Covered by a dedicated set of tests on top of the existing
suite (the new validator and the resolve-vs-review device-key authorization split).

**Missing** (see its own `CLAUDE.md`'s "Future work" for the full detail):
- `Report`, `Device`, `Geofence` entities — in the ER diagram, deliberately deferred (no
  consumer/committed endpoint yet).
- Real "own truck/route only" scoping for the `truck_driver` role — the ER model has no
  `User`↔`Driver`/`Truck` link to scope by, so this role currently gets unscoped read access.
- No refresh-token flow — `JWT_EXPIRE_MINUTES` is the only session-length control.
- No decided real-time push strategy for the live dashboard (currently: `ui-argus` polls
  `GET /api/routes/active` every 7s).

## 4. `ui-argus`

**Every screen now calls the real backend** — the fixture-era gap this section used to describe
is closed; `src/data/fixtures.ts` is deleted. `src/api/*` has one client module per resource
(`auth`, `me`, `users`, `trucks`, `drivers`, `routes`, `alerts`), all built on `src/api/client.ts`.
Role-based UI gating is real too: `RequireRole` gates `/access`, `Sidebar.tsx`'s nav items are
filtered per role, and `Fleet`/`Drivers`/`TravelManagement` render read-only for `guardian` (no
Add/Save controls) rather than just letting a write attempt 403 with no explanation. Two new
screens: **`Access.tsx`** (root_admin/admin user management, role-aware — an admin session only
ever sees/creates guardian accounts) and **`Profile.tsx`** (self-service profile edit for any
role). Full per-screen status: `src/ui-argus/INTEGRATION.md`.

The Live Operations dashboard and Alert Triage screen also match `backend-argus`'s unified
severity scale — tiles/legend/badges relabeled to Low/Medium/Critical everywhere (previously a
leftover 3-way `normal`/`low_vigilance`/`critical` vigilance split that didn't match `Alert`'s
own `critical`/`medium`/`low` scale, itself a remnant of the pre-binary-migration UI). Alert
Triage also now handles `ai_metadata: null` (a `panic_button` alert has no camera score) and
surfaces `grip_status`/`source`/escalation links/`resolved_at`.

**Still missing/open:**
- No Reports panel, no Geofence management, no dedicated Truck Driver screen — none of these
  were in the first UI pass (no committed API/table effort behind them).
- `Alert.media_url` — where captured clips are stored/served (S3? the backend directly?) isn't
  decided anywhere.
- **OSRM integration for `TravelManagement.tsx`'s create form — decided as a future
  iteration, not part of this pass.** `destination_coordinates`/`estimated_arrival` stay
  hardcoded (`{lat:0,lon:0}` / `null`) on route creation until then; this mirrors the same
  "mejora futura" framing the borrador doc and the top-level `CLAUDE.md` already give the OSRM
  service itself (still not part of any Docker Compose stack).
- No caching/data-fetching layer (TanStack Query or similar) — every screen does its own
  `useEffect` fetch, no shared cache/refetch-on-focus.
- A real-time push mechanism for `LiveOps.tsx` (currently polling, see backend section above).

## 5. `it-argus` (integration tests)

**Done, in `main`:** a real integration-test module, not just a plan for one — **Playwright**
browser tests driving the real `ui-argus` dev server against the real `src/backend-argus` +
MongoDB, together, rather than each module's own unit tests (which mock the other side away
entirely: the backend's tests never render a browser, the frontend's tests mock `fetch`). Its
own fully self-contained `docker-compose.yml` (own Mongo volume, own backend/UI builds, no
published host ports, real `healthcheck:` blocks) rather than an overlay on the root compose
file — see its own `CLAUDE.md`'s "Docker Compose" section for why, including a real networking
bug this caught (pointing Playwright at the internal DNS name `http://ui-argus:5173` silently
broke login, because `crypto.subtle` — which the real login path needs — is only available in a
browser "secure context," and that hostname isn't one; fixed via `network_mode:
"service:ui-argus"` so `http://localhost:5173` resolves straight to it).

**Re-verified for this pass, not just trusted from the doc:** `cd src/it-argus && docker compose
up --build --abort-on-container-exit` boots the full stack and all 4 specs in `tests/auth.spec.ts`
pass — `4 passed (9.8s)`: root-admin bootstrap login, a wrong-password inline error, an
unauthenticated deep link redirecting to `/login` and back after signing in, and sign-out
re-protecting a route. Matches this module's own `CLAUDE.md`/`README.md` claims exactly.

**Missing:** login was the first real thing connecting `ui-argus` and `backend-argus`, so it's
the only thing this suite covers — and that's now a real gap, not a future hypothetical, given
how far the real API integration has moved past login since (see section 4 above: every
`ui-argus` screen now calls the real backend, with real role-based gating across four roles).
None of Fleet, Drivers, Routes, Live Operations, Alert Triage, Access, or Profile have a
Playwright spec yet — this module's own `CLAUDE.md` flags "extending this suite to cover at
least one full round-trip per role" (an alert landing on a guardian's dashboard, an admin
blocked from `/access`'s root_admin-only actions) as the natural next step. Also worth being
precise about scope: `it-argus` proves the `ui-argus`↔`backend-argus` seam, nothing more — it
says nothing about the cv-argus→ESP32→backend chain (see section 7 below, "Nothing exercises the
full chain," which is still true for the edge side).

## 6. `src/dataset` (local dataset-creation pipeline)

**Done, in `main`:** a local, CPU-parallel, **pausable/resumable** reimplementation of four of
the ML pipeline's ten notebooks — `01_dataset_creation_lstm`, `02_dataset_creation_flat`,
`06_dataset_creation_face_crops`, `09_dataset_creation_cnn_lstm` — built to run on a WSL2/Linux
dev box instead of Colab, which kept interrupting the multi-hour extraction runs. **This is now
the source of truth for dataset creation**; those four notebooks are kept only as Colab-runnable
reference, not the thing to edit. Standalone module (`mediapipe` + `opencv` + `numpy` + `pandas`
+ `tqdm`, deliberately **no TensorFlow** — `argus_dataset/geometry.py` is a NumPy port of the
notebooks' `GeometricRatioFeatureLayer`, equivalence-tested against the real `tf.keras` layer to
`atol=1e-4` in `tests/test_geometry_equiv.py`, since dataset creation only ever *calls* that
layer and never serializes/deserializes it). Also includes a raw-video clip collector
(`scripts/collect_clips.py` — webcam recording or file import, no-overwrite naming, a
write-as-you-go provenance log) and incremental-update tooling (`scripts/update_dataset.py` —
classifies every raw clip as done/new/orphan against each artifact's own resume checkpoint) that
have no notebook equivalent at all. Has its own `README.md` (the practical runbook: extraction →
build → verify → publish → incremental updates) and `CLAUDE.md` (architecture, the pause/resume
design, the notebook-fidelity contract).

**Re-verified for this pass:** `pytest` (from `src/dataset`, after installing the `[dev]` extra
for `tensorflow`/`pyarrow`) → `39 passed, 2 skipped` — matches this module's own README/CLAUDE.md
claim exactly. The 2 skips are the geometry-equivalence and analysis checks that need
`tensorflow`/`scipy`, silently skipped without the `[dev]` extra rather than failing.

**Missing:**
- **Real MediaPipe inference is unverified on any dev box so far** — the environment(s) this was
  built and re-tested in lack the system shared libs (`libgles2`/`libegl1`/…) MediaPipe needs at
  import, so feature-*value* fidelity against a real Colab run (as opposed to schema/contract
  fidelity, which `scripts/verify_artifacts.py` does check) remains unconfirmed. This module's own
  "Same artifact means same schema, not same bytes" section already frames this as expected
  (MediaPipe/XNNPACK isn't cross-platform-deterministic), not a bug to fix.
- Real webcam capture in `collect_clips.py` is also unexercised end to end — no camera in the
  environment(s) this ran in — so only the file-import path (`test_collect.py`) has real test
  coverage; the capture loop itself is untested beyond code review.
- This closes an ML-pipeline *tooling* gap only (faster, resumable dataset creation) — it doesn't
  move any of the cv-argus/ESP32/backend/frontend gaps above, and doesn't change what's actually
  deployed in `src/cv-argus` (still `11_cnn_lstm_training_drive_pull.ipynb`'s model, per the root
  `CLAUDE.md`'s "Current deployment status").

## 7. Testing / E2E / integration strategy — the real open question

`it-argus` (section 5 above) closes part of this gap — the `ui-argus`↔`backend-argus` seam now
has real integration coverage — but the rest of the picture is unchanged: every module's own unit
tests are real but isolated (`backend-argus` uses `mongomock-motor` + an opt-in real-Mongo tier
via testcontainers; `cv-argus`'s Bluetooth layer is tested against `FakeTransport`, an in-memory
double, never a real socket), and **nothing exercises the full chain** — cv-argus → (simulated)
ESP32 → backend → a guardian actually seeing it on the dashboard.

**The idea on the table** (not built, discussed here for later): since both halves of the real
contract are already fully specified and testable in isolation (the Bluetooth protocol grammar
and the backend's HTTP/device-key contract), a **software-only simulator for either side**
becomes possible without waiting on real ESP32 hardware — e.g. a script that plays the ESP32's
role against real `cv-argus` `sender/` code over `FakeTransport` or a loopback socket, relays
what it receives to a real running `backend-argus` over HTTP, and — the specific scenario you
raised — running *several* such simulated trucks at once so a guardian watching the real
`ui-argus` dashboard sees realistic distributed fleet traffic, not just a single hand-crafted
request.

**Framework options, not decided — for discussion:**

| Option | Fit |
|---|---|
| **pytest + docker-compose** | Extends the pattern `backend-argus` already uses (testcontainers-backed integration tests) to also spin up simulated cv-argus/ESP32 processes against a live backend. Lowest new-tooling cost — reuses what already exists rather than adding a new test runner. |
| **A bespoke asyncio simulation script** (e.g. `scripts/simulate_fleet.py`) | N virtual trucks constructing real `Alert`/`StatusRoute` records (reusing `cv-argus`'s own `alerts/` models rather than reinventing serialization) and POSTing them to a live backend. Closest match to the "multiple trailers distributed" scenario you described, and the most direct route to a guardian watching real traffic on the live dashboard. |
| **Playwright** (official Python client) | Real browser-level E2E against `ui-argus` — verifies a guardian actually *sees* an alert land on the dashboard, not just that an API call returns 200. Complements, doesn't replace, the simulator idea above. |
| **Locust** | Normally a load-testing tool, but its "swarm of simulated users" model maps directly onto "swarm of simulated trucks" — could double as both a load test and a fleet simulator. |
| **Robot Framework** | A readable, less code-heavy acceptance-test DSL. Produces test reports a non-technical reader (e.g. a thesis reviewer) can follow, though it's a less idiomatic fit for an otherwise all-Python/TypeScript stack. |

None of this is built. Per the grading-criteria reality check above, it's a genuine quality
investment worth making, not a rubric requirement — worth deciding deliberately rather than
defaulting into whichever option is fastest to start.

## 8. Documentation itself

- Root `CLAUDE.md` said `docs/criterios/` in two places; the real folder on disk is
  `docs/criteria/` — fixed alongside this document. The same typo had also spread to
  `src/ui-argus/CLAUDE.md`'s "Stack choices" section (missed in that earlier pass) — fixed now.
- `docs/document/borrador-proyecto-modular-argus.md` (the working titulación draft) contradicted
  itself: Parte 4/Parte 5.1.2 still called the backend/frontend "planeado, sin implementar aún"
  in the "Tecnologías utilizadas" list, while Parte 1/5/6/8/9 elsewhere in the same file already
  cited `backend-argus`'s real FastAPI+MongoDB code and the 84.24%-accuracy model result as
  settled fact. Fixed — see that file's own tech-stack bullets, now consistent with the rest of
  the document.
- Two architecture documents exist for this project and disagree: the real one (matches the code
  + `semantic-design.drawio.xml` + `CLAUDE.md`) and an AWS-serverless one
  (`Argus_Definicion_Tecnica.docx.pdf`, dated separately). The borrador doc already demotes the
  AWS version to "possible future direction if time permits, not a commitment" — this roadmap
  only tracks the real, canonical architecture.
- This file's own "merged vs. not merged" section had gone stale — `src/backend-argus` and
  `src/cv-argus`'s `alerts`/`buffer`/`orchestrator`/`sender` modules were both already merged to
  `main` (their feature branches no longer even exist), but this file still framed them as
  sitting on branches. Fixed alongside the severity-taxonomy/grip-fusion updates above — a
  reminder to actually check `git log`/`git branch` against a merge claim here rather than
  trusting the last time this file was written.
- Root `CLAUDE.md` had a stale sentence claiming the steering-wheel grip sensor "feeds the same
  decision orchestration node" as the Pi's AI orchestrator — every other doc (this file included,
  now) agrees grip wires to the ESP32 only, which is the actual fusion point since it's the only
  device with both the camera classification and the grip reading. Fixed.
- This file itself never mentioned `src/it-argus` or `src/dataset` at all, despite both being
  real, tested, `main`-merged modules — sections 5 and 6 above added, each re-verified against a
  real test run rather than trusted from their own docs (`it-argus`: `4 passed` via
  `docker compose up --build --abort-on-container-exit`; `src/dataset`: `39 passed, 2 skipped`
  via `pytest`). Root `CLAUDE.md` also had one stale leftover from the pre-real-backend `ui-argus`
  era — "markers from fixture coordinates" in its architecture-overview bullet, contradicted by
  its own later "Repository state" section saying fixtures are gone — fixed. `src/backend-argus/
  CLAUDE.md`'s "Current status" section still said the hermetic suite had "grown to 48 tests" and,
  two sentences later in the very same paragraph, that `ui-argus` "doesn't call any real API yet"
  — both stale (actual: 53 tests passing as of this pass; every `ui-argus` screen has called the
  real API since the RBAC/severity-taxonomy work, as the paragraph immediately above it already
  says) — fixed.
