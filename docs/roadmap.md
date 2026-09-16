# Argus roadmap — what's missing, module by module

A living gap-analysis document: what exists, what's merged vs. sitting on a branch, and what's
genuinely not built anywhere. Grounded in `docs/` (the borrador project doc, the ER and
semantic-design diagrams, the grading-criteria PDF) cross-referenced against the real code
across `main` and the branches in flight as of this writing. Update this file as gaps close —
don't let it drift the way some of the per-module docs briefly did (see the "documentation
itself" section at the bottom for that story).

## Read this first: merged vs. not merged

**Update**: both pieces this section used to describe as unmerged are now in `main` —
`src/backend-argus` and `src/cv-argus`'s `alerts/`/`buffer/`/`orchestrator/`/`sender/` modules.
This section's "not merged yet" framing was stale (the merges happened in commits `6a3a855`,
`645ef05`, `dea719c`); flagged here rather than silently rewritten so the correction itself is
visible. Sections 1 and 3 below still describe what those modules contain — read the "done" bullets
there as simply "done, in `main`," not "done, on a branch."

Everything below distinguishes "missing from the project" (nobody has built it) from "missing
from what's built" (it's real code, just not everything it needs yet).

## Módulo 3 (Sistemas Distribuidos) grading reality check

`docs/criteria/criteriosaprobacion_0.pdf` requires **architecture**: real-time communication
between at least two devices, a justified client-server/peer-to-peer algorithm, justified
protocols (3.1–3.4). It does **not** require an automated test suite, E2E tests, or a simulation
deliverable — that bar (section 5 below) is a quality goal this project is choosing for itself,
not something the grading rubric asks for. Worth keeping in mind so the testing-framework
decision doesn't get overbuilt chasing a requirement that isn't actually there. The rubric does
warn that a plain FastAPI backend + HTTP clients "no cuenta por sí solo como sistema
distribuido" — the Pi↔ESP32 Bluetooth leg (section 2) is what actually satisfies this, and the
*polling algorithm's* failure-handling detail (what happens if Bluetooth drops mid-pull, how
`sent`/`unsent` is tracked) is explicitly the level of detail the rubric expects documented in
the report, not just "we used Bluetooth."

## 1. `cv-argus` (Raspberry Pi edge)

**Done, merged:** `model/` (fused CNN-embedding + geometric-feature + LSTM classifier, 84.24%
measured accuracy / 0.8375 macro-F1, one held-out split — see its own `CLAUDE.md` for the full
caveats) and `pipeline/` (threaded Stage/Pipeline abstraction, MediaPipe stages, camera sources).

**Done, not merged** (`worktree-cv-argus-alert-pipeline`): `alerts/` (Alert/AlertKind data model
+ serialization), `buffer/` (WAL-mode SQLite queue, concurrency-tested), `orchestrator/`
(debounce/cooldown decision loop + heartbeat), `sender/` (a custom Bluetooth SPP protocol — see
section 2 below — fully implemented and unit-tested against a fake transport, `FakeTransport`).

**Missing, even once merged:**
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
  all** — see section 2. `cv-argus`'s own `CLAUDE.md` says so explicitly.

## 2. ESP32 firmware — missing entirely

No code exists anywhere in this repo. This is the single biggest concrete gap, and it's the
device that owns everything actuation-/safety-critical per the root `CLAUDE.md`: the alarm
speaker, the CAN bus/AEB actuator, the panic button, the geolocation (GPS) module, and — per the
`semantic-design.drawio.xml` diagram — the steering-wheel grip sensor. None of those five have
any code anywhere, on either the Pi or ESP32 side; they're drawn as distinct boxes in the
diagram and explicitly out of `cv-argus`'s scope.

**What it needs to implement, against contracts that already exist and are already tested:**

1. **Bluetooth SPP client**, speaking the exact protocol `cv-argus/src/sender/protocol.py`
   already implements server-side (see `src/esp32-argus/README.md`, new, for the full grammar).
2. **HTTP relay to `backend-argus`**, using a per-truck device API key (`X-Device-Api-Key`
   header) against `POST /api/alerts` and `POST /api/routes/{id}/status` — this auth scheme was
   built specifically anticipating this caller; see `src/backend-argus/CLAUDE.md`'s "Device
   (ESP32) auth" section.
3. **Attaching real GPS coordinates** to every record it relays — `cv-argus` never populates
   `geolocation` itself (no GPS on the Pi in this design); the ESP32 is expected to fill
   `coordinates`/`current_coordinates` before the HTTP call, not after.
4. **Grip sensor, panic button, CAN bus/AEB actuator, alarm speaker** GPIO/UART integration —
   entirely new hardware-interfacing work, no existing code to build on.

New `src/esp32-argus/README.md` documents the exact protocol/contract details so this can start
without re-deriving them from `cv-argus`'s and `backend-argus`'s source.

## 3. `backend-argus`

**Done, in `main`**: FastAPI + MongoDB (Beanie) covering User, Truck, Driver, Route,
Status_Route, Alert + `/api/auth/login`. **RBAC is now four roles, not three** — `admin` was
added as a deliberate product decision: an operations role with write access on Truck/Driver/
Route, plus one narrow, server-enforced exception to otherwise-zero user-management access (it
may create/edit/deactivate `guardian`-role accounts only — see that module's `CLAUDE.md`'s "RBAC
— four roles" for exactly how it's scoped, including why non-guardian targets 404 rather than
403). Also new: self-service `GET`/`PUT /api/auth/me` so any role can edit their own email/name/
phone/password without needing `/api/users` access. A device-API-key auth path for the ESP32,
`GET /api/routes/active` for the live dashboard. Verified: hermetic pytest tier passes (44 tests
as of this change), a full Docker stack (backend + Mongo + `ui-argus`) has previously booted and
round-tripped real HTTP calls.

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

**Still missing/open:**
- No Reports panel, no Geofence management, no dedicated Truck Driver screen — none of these
  were in the first UI pass (no committed API/table effort behind them).
- `Alert.media_url` — where captured clips are stored/served (S3? the backend directly?) isn't
  decided anywhere.
- OSRM integration for `TravelManagement.tsx`'s create form (`destination_coordinates`/
  `estimated_arrival` are still stubbed).
- No caching/data-fetching layer (TanStack Query or similar) — every screen does its own
  `useEffect` fetch, no shared cache/refetch-on-focus.
- A real-time push mechanism for `LiveOps.tsx` (currently polling, see backend section above).

## 5. Testing / E2E / integration strategy — the real open question

Nothing above the unit level exists today, across any module. Every module's own tests are real
but isolated: `backend-argus` uses `mongomock-motor` + an opt-in real-Mongo tier via
testcontainers; `cv-argus`'s Bluetooth layer is tested against `FakeTransport`, an in-memory
double, never a real socket. **Nothing exercises the full chain** — cv-argus → (simulated)
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

## 6. Documentation itself

- Root `CLAUDE.md` said `docs/criterios/` in two places; the real folder on disk is
  `docs/criteria/` — fixed alongside this document.
- `docs/document/borrador-proyecto-modular-argus.md` (your own working draft) still lists the
  backend as "Pendiente" — now stale, since `backend-argus` exists. Flagged here rather than
  edited, since that file has local uncommitted changes in your own checkout this worktree can't
  see.
- Two architecture documents exist for this project and disagree: the real one (matches the code
  + `semantic-design.drawio.xml` + `CLAUDE.md`) and an AWS-serverless one
  (`Argus_Definicion_Tecnica.docx.pdf`, dated separately). The borrador doc already demotes the
  AWS version to "possible future direction if time permits, not a commitment" — this roadmap
  only tracks the real, canonical architecture.
