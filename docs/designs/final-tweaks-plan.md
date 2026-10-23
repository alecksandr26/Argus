# Argus final tweaks — Pi ↔ ESP32 ↔ backend ↔ UI (multi-team plan)

## Context
The last round of changes connecting the Raspberry Pi (`src/cv-argus`), the ESP32
(`src/esp32-argus`), the backend (`src/backend-argus`) and the dashboard (`src/ui-argus`).

Goals:
- fewer, non-repeating messages, with one alert per incident;
- GPS on every message, in one combined report;
- a priority sender on the ESP32: live first, dead letters last;
- guardians own closing alerts, and incidents are grouped per route;
- a manual stop for routes;
- vision fixes: one face, no head-sized "eyes".

Problems today:
- The no-face ladder sends 2 alerts.
- The dead-letter queue (DLQ) drains oldest first and blocks live messages for 10–25 s.
- A resolve is lost while its alert sits in the DLQ.
- A replayed alert is stamped with the *current* route (`esp32-argus/.../backend_messages.cpp:100`).
- Positions only go out every 60 s.
- Alerts aren't grouped.
- The detector outputs head-sized eye boxes, because 56 % of its training images are full-image eye close-ups.

The plan was reviewed twice against the code by fresh agents. Their findings are folded in below.
The work is split across **6 teams plus a team lead**, each team in its own worktree and branch.

## 1. Decisions (agreed with the user)

### Pi: detection
- **No face:** one CRITICAL after 5 s, with no low/medium steps before it.
- **One alert per incident:** MEDIUM once, plus one CRITICAL upgrade if it gets worse. A reason change at the same level sends nothing.
- **Driver recovered** (about 2 s of a clear face): send **one OK** ("driver OK again").
- **Keepalives:** Pi → ESP32 every 2 s. They never reach the backend.
- **Heartbeat:** the 60 s `route_status` heartbeat is **removed**.
- **One face:** use the largest face and lock onto it; other faces are ignored.
  - If the locked face is lost while another face is visible, keep counting no-face.
  - **Switch after 5 s.** The switch resets timers and the tracker only, never the held severity.
- **Eyes:** add an inference size/position filter, plus notebook 12 retraining changes (the user runs Colab).

### ESP32: sending
- **Panic button removed:** the button, its NVS queue and the panic SMS. The SIM800L stays for mobile data.
- **GPS:** read every 1 s.
  - With no fix, send the last good position (saved in NVS, throttled), marked "last known" with its age.
- **One combined report per POST:**
  - the GPS points since the last report, plus
  - at most one event (alert, upgrade or driver OK).
- **When reports go out:**
  - An event is sent immediately.
  - Otherwise a report goes 5 s after the previous one ends.
  - Pending live events retry after at least 1 s.
- **Event priority:** live CRITICAL > live MEDIUM > live OK > DLQ critical (only when no live event is pending).
  - A higher live event preempts a lower one, but only between AT steps.
  - A critical never preempts a critical.
  - Any live event preempts a DLQ replay. The replayed row goes back to the DLQ, and that attempt is not counted.
- **Attempt timeouts:** WiFi 8 s; mobile 8 s on the first attempt, 15 s on retries; plus 10 s for modem setup after power-on or a lost signal.
- **Live retry windows:**

  | Event | Retries for | Then |
  |---|---|---|
  | CRITICAL | 60 s | DLQ |
  | MEDIUM | 30 s | dropped |
  | OK | 30 s | dropped |

- **GPS points of a failed report are dropped** (user decision). The path has gaps after failures.
- **DLQ:**
  - Holds **only CRITICAL** events.
  - Replayed newest first, round-robin so one row can't use up its attempts back to back.
  - Each attempt is one try.
  - **Every attempt counts** (user decision), including ones made while there's no network. The live failure that dead-letters a row is attempt 1.
  - After **5 failed attempts** the row moves to the Pi `archive` table. It is never deleted and never resent.
  - Consequence: with the SIM800L, a critical is archived after about 80 s offline.
- **Logs:** USB Serial, one line per operation, tagged `[live]` or `[dlq]`, with attempt, transport, duration and result.

### Backend and UI
- **Delivery tag:** events carry `delivery: live|dlq` and `queued_seconds`.
- **Closing:** only **guardian + root_admin** close alerts.
  - The device OK only sets `driver_ok_at`.
  - A note is **required** to close a critical.
- **Incidents:**
  - Medium and critical alerts of one route form one incident when they are less than **15 min** after the incident's last alert.
  - The window restarts with each alert, and is measured by `occurred_at`.
  - A late alert never joins a closed incident.
  - Closing an incident closes all of its alerts.
- **Route state** (this drives the map marker colour): after a medium/critical, the route keeps that state until either:
  - a guardian closes the incident, or
  - there has been **1 h of only-OK GPS points** (vigilance `low`, no gap longer than 60 s; silence or `unknown` doesn't count).

  The alerts stay open either way.
- **Incident feed** (Live ops and route view): grouped by route, in tiers **Critical → Medium → Old (> 1 h since the last alert)**, newest first within each tier. Alert history shows closed incidents.
- **Replayed criticals:** shown as "recovered after lost connection".
- **Positions:** every alert shows its position everywhere, with a "last known (N old)" label when that applies. The route view draws the GPS path.
- **Polling:** the dashboard polls every **5 s** and refreshes only the data, not the page.
- **Ending a route:** "Complete trip" (exists) plus a new **"Stop route"** with a required reason.
  - Available to root_admin + admin.
  - Open incidents stay open.
  - After the end, the backend **rejects everything** from the device for that route; rejected criticals are archived on the Pi.
- **Simulator:** aligned with all of the above, and posts the same combined report.

## 2. Shared contracts (written first by the lead, frozen before coding)
The contracts live in `docs/designs/final-tweaks-contracts.md` on the base branch. They change
only through the lead, at a sync.

### C1: Pi ↔ ESP32 Bluetooth v2 (not compatible with v1)
- **Session grammar:**
  - Commands: `PULL n`, `DLQ 1`, `CONFIG`, and sessions that open with standalone `FAIL <id> <reason> <json>` / `ACK <ids>` lines and end with `DONE`.
  - `END <dlq waiting> <outbox waiting>`.
  - `TAKEN <ids>` after `END`: the Pi leases those rows to the ESP32 for 120 s. Leased rows are skipped by `expire_outbox` and `fetch_unsent`. When a lease expires, the row returns to the outbox.
- **Records:** carry `severity`, `previous_severity`, `episode`, `reason`, `transition`, `class_name`, and `age_s` (from `mono_ms`/`boot_id`).
- **DLQ contents:** critical raises and escalations only. The DLQ hands out one row at a time.
- **FAIL reasons:** `route_finished`, `too_old` and `no_route` archive at once. Any other failure increments `attempts`, and 5 attempts archive the row.
- **What's gone:** `route_status` records.
- **`PartsRuleEngine.reset_signals()`:** resets the timers only, never the held severity.

### C2: ESP32 → backend
- **Endpoint:** `POST /api/devices/me/report`. It is the only device write, and the simulator uses it too.
  - Body: `{ points: [{age_s, lat, lon, speed, vigilance: low|medium|critical|unknown, last_known, location_age_s}] (≤ 10), event?: {client_event_id, episode, kind: alert|ok, severity, alert_type, age_s, delivery, queued_seconds, position?, speed, ai_metadata} }`.
  - Times are relative (`age_s`), and the backend computes absolute times from its own clock.
  - A position is optional on an event; with none, it's labelled "position unknown".
- **Response:** **200 with a result per part** once auth and the envelope are valid: `{points:{stored, route, status: ok|no_active_route|route_finished}, event:{status: created|duplicate|rejected, reason: route_finished|too_old|wrong_truck|invalid, id_alert}, active_route}`. The body is minimal.
- **How the ESP32 reacts:**

  | Response | Reaction |
  |---|---|
  | event created / duplicate | ACK to the Pi |
  | event rejected | FAIL → archive (critical), or drop (medium/OK) |
  | points `no_active_route` / `route_finished` | take `active_route` from the response |
  | 401 | re-CONFIG |
  | 429 / 5xx / transport error | the attempt failed (retry rules) |

- **Event route:** the backend picks it by time. It's the truck's route whose `actual_departure..actual_arrival` contains `occurred_at`; otherwise the event is rejected `route_finished` / `no_route`.
- **Points:** go to the truck's current active route.
- **Driver OK:** matched by `(id_route, episode)`. It is stored even if the alert arrives later.
- **Deduplication:** unique partial index on `(id_route, client_event_id)` for alerts and `(id_route, timestamp)` for points, using `insert_many(ordered=False)`.
- **Auth:** one bcrypt check per request, plus a warm-container cache from key fingerprint → truck.

### C3: Backend → UI
- **`Incident`:** `id_incident`, `id_route`, `severity`, `first_alert_at`, `last_alert_at`, `alert_count`, `driver_ok_at`, `has_replayed`, `closed_at`, `closed_by`, `close_note`, `route_ended`.
  - Grouping uses a per-route lock: `findOneAndUpdate` on the Route.
- **`Route`:** adds `incident_state: critical|medium|fine` (kept up to date on ingest via `last_not_ok_at`), plus `stop_reason`, `stopped_by`, `stopped_at`. Status `cancelled` is used for a stop.
- **Endpoints:**
  - `GET /api/live?since=<server_time>` returns active routes (state, latest position), **all open incidents in full** (tier-sorted, each alert with its position) and track points received since the cursor, plus `server_time`.
  - `GET /api/live?route=<id>` serves one route.
  - `GET /api/routes/{id}/track` gives a closed route's history, downsampled.
  - `POST /api/incidents/{id}/close {note}` (guardian + root_admin).
  - `POST /api/routes/{id}/stop {reason}` (root_admin + admin).
- **New fields** on `Alert` / `StatusRoute`: `delivery`, `queued_seconds`, `driver_ok_at`, `id_incident`, `location_is_last_known`, `location_age_s`, `position_unknown`.

### C4: Constants
| Constant | Value |
|---|---|
| `RULE_NO_FACE_CRITICAL_S` | 5 |
| `FACE_SWITCH_AFTER_S` | 5 |
| `INCIDENT_GROUP_WINDOW` | 15 min |
| `ROUTE_STATE_CLEAR_AFTER` | 1 h |
| `OK_MAX_GAP` | 60 s |
| `OLD_INCIDENT_AFTER` | 1 h |
| `REPORT_INTERVAL` | 5 s |
| `MIN_SEND_GAP` | 1 s |
| Attempt timeouts | as in section 1 |
| Live retry windows | 60 / 30 / 30 s |
| `DLQ_MAX_ATTEMPTS` | 5 |
| `REPORT_MAX_POINTS` | 10 |
| `UI_POLL_MS` | 5000 |
| GPS retention | 7 days full, then 1 point/min |

## 3. Teams
Each team has 1 implementer and 1 reviewer, and its own worktree and branch. The base branch
`feat/final-tweaks` (this worktree, renamed) holds the contracts commit. Every team branches from
it as `feat/final-tweaks-<team>` in `.claude/worktrees/ft-<team>`, and **edits only the paths it
owns**.

### T1: Pi messaging
- **Owns:** `cv-argus/src/orchestrator/`, `buffer/`, `sender/`, `alerts/`; the `RULE_*`/`ORCHESTRATOR_*`/`BUFFER_*` constants; `tests/test_constants.py`; `cv-argus/CLAUDE.md` + README (T2 sends its sections through the lead).
- **Scope:**
  - **Rules:** no-face goes to a single critical; the recovery reading sends its class at once; the heartbeat is removed; add `reset_signals()`.
  - **Buffer:** critical-only DLQ, newest-first round-robin, `archive` table (also used by `cap_unsent` instead of deleting), `age_s`, `buffer.status` shows the archive.
  - **Protocol:** C1 server side (grammar, `TAKEN` lease, `END` counts).
  - **Migration:** move existing non-critical and `route_status` DLQ rows to the archive.

### T2: Vision
- **Owns:** `cv-argus/src/pipeline/face_detector_stage.py`, `parts_stages.py`, `model/`; the `PARTS_*` / `FACE_*` constants; `notebook/12_tiny_grid_detector.ipynb`; `src/dataset/` if needed.
- **Scope:**
  - **Face lock:** largest face + sticky lock; switch after 5 s calls `reset_signals()`.
  - **Eye filter:** before NMS, drop eye boxes larger than `PARTS_EYE_MAX_FRAC=0.30` or with a centre outside `PARTS_EYE_CY_RANGE=0.15–0.70`. Mirror it in notebook cell 26.
  - **Notebook augmentation:** shrink close-ups onto a mean-gray + noise background, add zoom-out, and drop oversized eye labels on face crops.

### T3: ESP32
- **Owns:** `src/esp32-argus/**`, `src/tests/edge_chain/`, `src/tests/smoke_backend.py`.
- **Remove:** panic (call `modemBegin()` from `setup()` instead).
- **GPS:** 1 s reads; last known position in NVS (saved every 60 s or on losing the fix, together with the fix's GPS time).
- **`senderTask`:**
  - The only code that touches the modem/HTTP; cached registration state.
  - Mutex for shared state; static queue slots; `vTaskDelay(1)` in busy-waits; logs heap and stack high-water mark.
  - Report building, priority, preemption between AT steps, retry windows, DLQ rules, logs.
  - A real deadline per attempt (today `modemReadUntil` only has an idle timeout).
  - Drain a late `+HTTPACTION`.
- **Protocol:** C1 client side.
- **Report response:** handle it as in C2.
- **Pure policy logic** goes in host tests (`test/host/run.sh`, g++).
- **edge_chain mirror:** update it (run in the lead's integration worktree).

### T4: Backend
- **Owns:** `src/backend-argus/**`, `src/infra-argus/**`.
- **Scope:**
  - **Endpoints:** `/api/devices/me/report` (C2), `/api/live`, track history, Incident model (grouping, close with note), stop route.
  - **Route state:** `incident_state` / `last_not_ok_at`.
  - **Driver OK:** stored by episode.
  - **Dedup:** the unique indexes.
  - **Auth:** cache.
  - **GPS retention:** partial TTL (one `keep=true` point per minute; others expire after 7 days).
  - **Missing `vigilance`:** `unknown` instead of defaulting to `low` (`schemas/status_route.py:24`, `models/status_route.py:23`).
  - **Rejecting finished routes:** the resolve path too (`alerts.py:218`).
  - **Panic:** kept for reading, 422 on create.
  - **Migration script:** device `resolved_at` → `driver_ok_at`; `id_incident` for historical alerts.
  - **Infra:** update the guardrail comments for the new cadence.
  - **Kept for now (removed later):** the device PUT of `resolved_at` and panic create, so the branch stays green until T6 merges.

### T5: UI
- **Owns:** `src/ui-argus/**`.
- **Scope:** rework around incidents and positions.
  - Types (C3).
  - One `/api/live` poll every 5 s; paused when the tab is hidden; `AlertHistory` and `SimulatorControl` polling set to 5 s / 10 s.
  - Incident feed with tiers (Live ops + route view).
  - Every alert card shows its position (coordinates, "show on map", last-known label).
  - **Map:**
    - marker colour = `incident_state`;
    - alert pins and the GPS path;
    - last-known marker style;
    - marker key `id_route`.
  - `ALERT_TYPE_LABEL`.
  - Replayed badge.
  - Close dialog (note required for critical).
  - "Driver OK since".
  - Positions in alert history and route history.
  - Complete / Stop route dialogs (Travel management, Live ops, route view).
  - Remove the panic UI.
  - vitest.

### T6: Simulator + integration
- **Owns:** `src/simulator-argus/**`, `src/it-argus/**`.
- **Scope:**
  - **Simulator:** no panic; uses `/api/devices/me/report` (5 s reports, one alert per incident, driver OK).
  - **it-argus specs:** incident grouping, close with note, stop route rejects reports, and position labels (run in the lead's integration worktree).

### Lead
- **Owns:** the contracts doc, root `CLAUDE.md`, `docs/roadmap.md`, root `docker-compose.yml`.
- **Scope:** writes the contracts, checks alignment at every sync, answers cross-team questions, runs the integration worktree, writes the final docs (the "Missing alerts" section: what's built now).

## 4. Coordination
- **Run as a Workflow** (the user opted into multi-agent orchestration): 13 agents (the lead plus 6 teams × 2), roughly 40–50 agent runs over 3 rounds.
- **Phase 0, contracts:** the lead writes C1–C4, commits them on `feat/final-tweaks`, and pushes.
- **Rounds 1–3, build:**
  - Each implementer works in its own worktree, commits small changes (no Co-Authored-By trailers) and runs its module's tests.
  - It reports back: commits, contract questions, and notes for the other teams.
  - Round 1: data model, protocol, core logic + tests.
  - Round 2: wiring, UI, the remaining scope.
  - Round 3: review fixes, docs, green tests, and the integration-only suites.
- **Sync after each round:**
  1. Each reviewer reviews its team's commits against the goal and the contracts, and reports findings and cross-team notes.
  2. The lead consolidates the reports, resolves conflicts and amends the contracts if needed (one commit on the base branch; teams rebase onto it).
  3. The lead sends every team a sync brief, which goes into the next round's prompts.
- **Conflict rules:** follow the path ownership above. `constants.py` is split by prefix (T1 / T2). No team edits another team's paths.
- **Final:**
  - The lead runs every suite on every branch.
  - In a local, test-only integration worktree (never pushed), the lead runs backend + UI + simulator + edge_chain + it-argus together.
  - Each team branch is pushed and opened as a **draft PR into `feat/final-tweaks`**.
  - Merge order: (T4 + T6 together) → (T1 + T2 + T3 together, since the protocol changes) → T5. The user merges.

## 4b. Test updates (unit + integration) — required, not optional
**Rule for every team:** a change is done only when (1) the existing tests it breaks are
**updated** to the new behaviour (never deleted just to go green; a test is removed only when the
feature it covers is removed, e.g. panic, heartbeat), (2) new tests cover the new behaviour, and
(3) the module's full suite passes. Each reviewer checks this at every sync; the lead re-runs
every suite at the end.

| Team | Existing tests to update | New tests |
|---|---|---|
| T1 | `cv-argus/tests/test_orchestrator_parts_rules.py` (no-face ladder → single critical), `test_orchestrator_readings.py` (heartbeat, recovery class), `test_orchestrator_decision.py`, `test_orchestrator_lifecycle.py`, `test_buffer_store.py` (states, `cap_unsent` → archive), `test_sender_dlq.py` (oldest-first → critical-only newest-first round-robin), `test_sender_protocol.py` + `test_sender_server.py` (v2 grammar, `TAKEN`, `END` counts, standalone FAIL), `test_alerts_models.py` (no `route_status`), `test_constants.py` | archive table + migration of old DLQ rows, lease expiry, attempt counting, `reset_signals()` |
| T2 | `test_pipeline_face_detector_stage.py`, `test_pipeline_parts_stages.py`, `test_model_parts_detector.py` (decode parity), `test_pipeline_parts_logging.py` | largest + sticky face, 5 s switch keeps held severity, eye size/position filter |
| T3 | `esp32-argus/test/host/*` + `test/vectors/*` (protocol/fusion vectors), `test/hil/*` (remove panic, heartbeat → reports), `src/tests/edge_chain/*` (`esp32_sim.py` mirror: v2 protocol, report endpoint, no panic/route_status), `src/tests/smoke_backend.py` | host tests: priority, preemption, retry windows, DLQ attempts, report build/response handling, last-known GPS |
| T4 | `backend-argus/tests/test_alerts.py`, `test_alert_resolution.py` (resolve → `driver_ok_at`, guardian close), `test_status_routes.py` (`unknown` vigilance), `test_ingestion_validation.py`, `test_idempotency_and_time.py` (unique indexes), `test_route_lifecycle.py` + `test_routes.py` (stop, finished-route rejects resolve), `test_rbac.py` (close: guardian + root_admin; stop: root_admin + admin), `test_device_auth.py` (cache), `test_seed_dev_data.py` (no panic seed), `test_simulator.py`; `infra-argus/test/guardrails.test.ts` | report endpoint (partial results, dedup race, by-time route, wrong truck), incidents (grouping, close note), route state, `/api/live`, TTL keep flag, migration script |
| T5 | `ui-argus/src/utils/status.test.ts` (labels), `utils/format.test.ts`, `pages/TravelManagement.test.tsx` (Complete/Stop route), `pages/Fleet.test.tsx`, `components/SimulatorControl.test.tsx` (poll interval), `components/StatusPill.test.tsx` | incident feed tiers + sort, incident card with position/last-known label, close dialog (note required), stop dialog, `/api/live` delta merge |
| T6 | `simulator-argus/tests/test_client.py` + `test_virtual_truck.py` (report endpoint, no panic, one alert per incident); `it-argus/tests/live-ops.spec.ts` (panic test → fusion alert, review → incident close with note), `helpers.ts` (report helper), `trucks.spec.ts` if routes/devices change | it-argus: incident grouping, close with note, stop route rejects reports, positions shown with labels |

## 5. Verification
- **T1/T2:** `cd src/cv-argus && pytest`. Covers the rules, readings, critical-only + round-robin DLQ, archive (including from `cap_unsent`), lease, the session grammar, `age_s`, the face lock and 5 s switch, and the eye filter.
- **T3:** `src/esp32-argus/test/host/run.sh`. Covers priority, preemption points, retry windows, DLQ attempt counting, report building, response handling, last-known GPS, and C1 parsing.
- **T4:** `pytest` in `src/backend-argus`. Covers:
  - the report endpoint: partial results, dedup under a retry race, wrong truck, by-time route, finished route rejects points + events + resolve;
  - grouping: 14 min joins, 16 min starts a new incident, the window restarts, never joins a closed incident;
  - closing needs a note for a critical; the driver OK never closes; driver OK before its alert;
  - route state: 1 h of OK, and not cleared by silence or `unknown`;
  - TTL keep flag; migration; panic read-only;
  - `cdk synth` tests in `src/infra-argus`.
- **T5:** `npm test` + `npm run build` in `src/ui-argus`.
- **T6 + lead:** simulator tests; an integration run where incidents are grouped and tier-sorted, positions are labelled, the path is drawn, and a stop rejects reports; edge_chain; it-argus Playwright.

## 6. Steps only the user can do (after the PRs)
1. **Deploy the backend** (`cdk deploy`), then run the migration on Atlas and create the TTL index.
2. **Flash the ESP32** from the Arduino IDE (Windows, COM3) **and deploy the new Pi image together**: Bluetooth v2 is not compatible with v1.
3. **Bench HIL:**
   - POST duration per transport;
   - cold GPS fix time;
   - backend down → 60 s of retries → DLQ → 5 attempts → archive;
   - a live alert during a DLQ replay;
   - heap and stack logs.
4. **Retrain the model:** run notebook 12 in Colab, share the model files, update the Drive ids and filenames in `src/cv-argus/src/constants.py`, and rebuild the image.
5. **Pi end to end:**
   - Cover the camera 5 s → one critical with GPS.
   - Uncover → one OK; the alert stays open and the route stays critical.
   - A guardian closes it with a note → the route goes back to fine.
   - A second person in the frame → no effect.

## Out of scope
Reports on replayed alerts; retrying archived criticals; a "truck lost connection" alert; OSRM
route lines.
