# esp32-argus — not built yet

This module doesn't exist. No firmware, no code, nothing — this file exists so whoever starts
it (a future session, or you) has the real contracts already implemented on both sides to build
against, instead of re-deriving them from `cv-argus`'s and `backend-argus`'s source each time.
See `docs/roadmap.md` for how this fits the rest of the project's gap list.

Per the root `CLAUDE.md`, this is the **"Message Sender Orchestrator"**: the device that owns
everything actuation-/safety-critical in the truck cabin — the alarm speaker, the CAN bus/AEB
actuator, the panic button, the geolocation (GPS) module, and the steering-wheel grip sensor —
and is the only device that talks to the cloud backend. It has two jobs: pull queued
alert/status records off the Raspberry Pi over Bluetooth, and relay them to the backend over
HTTP with its own GPS reading attached.

## 1. Bluetooth SPP client — pulling from `cv-argus`

`cv-argus`'s `sender/` module (merged to `main` — fully implemented and unit-tested against a
fake transport) runs a Bluetooth SPP **server** on the Pi. This device is the **client**: it
connects, and initiates every exchange — the Pi never pushes. Exact grammar
(`src/cv-argus/src/sender/protocol.py`):

```
you  -> Pi:   PULL <n>\n
Pi   -> you:  one compact JSON line per unsent Alert record, oldest first, up to n rows
Pi   -> you:  END\n                         (sent even if 0 rows matched)
you  -> Pi:   ACK <id1>,<id2>,...\n         (only if the Pi sent ≥1 row)
Pi   -> you:  ACKED <n>\n                   (n = rows the Pi actually marked sent)
```

Case-sensitive, newline-delimited (no embedded newlines in the JSON lines). **Don't ACK unless
you've actually relayed those records onward over HTTP (section 2) and gotten a success
response** — the Pi only marks a row `sent` after your `ACK`, so if the HTTP relay fails after a
successful pull, the safe move is to *not* ACK that pull; the same unsent rows will be served
again on your next `PULL`. Rows are delivered at-least-once, never silently dropped — dedup by
`id` (below) is explicitly the backend's job, not this protocol's.

**The JSON record shape** (`cv-argus/src/alerts/models.py`'s `Alert` dataclass):

```json
{
  "id": "a1b2c3d4...",             // uuid4 hex — use as an idempotency key against the backend
  "kind": "drowsiness",            // or "route_status"
  "level": 2,                      // 1=Not Drowsy / 2=Drowsy, null for route_status
  "created_at_ms": 1234567890123,  // epoch ms, wall clock
  "source_id": "cam-0",            // camera/stream id — NOT a route id, see the gap below
  "payload": { "...": "..." },     // shape depends on kind, see below
  "geolocation": null               // ALWAYS null coming from cv-argus — see section 3
}
```

`payload` by `kind`:
- `"drowsiness"` → `{"class_name": "Drowsy" | "Not Drowsy", "probabilities": [p_not_drowsy, p_drowsy]}`
- `"route_status"` → `{"status": "OK"}` — this is genuinely all cv-argus produces for a status
  ping; see the gap notes in section 2 for what that means for `current_speed`/`odometer`.

## 2. HTTP relay to `backend-argus`

`backend-argus` (merged to `main` — real, tested code) already has endpoints built and auth
designed specifically anticipating this device as the caller — see
`src/backend-argus/CLAUDE.md`'s "Device (ESP32) auth" section for the full rationale. What you
need to do:

- **Auth**: send `X-Device-Api-Key: <key>` on every request. The key is per-truck, generated
  once via `POST /api/trucks/{id}/rotate-key` (an admin action, not something this firmware
  does itself), provisioned into your firmware config out of band. It's checked against the
  specific truck that owns the route you're posting to — a stolen key only works for its own
  truck's data.
- **`kind: "drowsiness"` records** → `POST /api/alerts`
- **`kind: "route_status"` records** → `POST /api/routes/{id}/status`

**Field mapping — what's a direct translation vs. genuinely undefined (be honest about this,
don't guess a wrong answer into firmware code):**

| Backend field (`AlertCreate`) | Source | Status |
|---|---|---|
| `ai_metadata.scores.not_drowsy`/`.drowsy` | `payload.probabilities[0]`/`[1]` | ✅ direct — required (and only meaningful) when `source == "fusion"`; the whole `ai_metadata` object is omitted/`null` for a `panic_button` alert, not just its sub-fields |
| `coordinates`, `speed_at_event` | your own GPS/speed reading | ✅ direct (this device's job — `geolocation` is always `null` from cv-argus, see section 3) |
| `id_route` | — | ❓ **undefined.** `cv-argus`'s `Alert.source_id` is a camera/stream id, not a route id. Nothing today tells this device which `Route` document a given truck's alert belongs to. Needs a real answer before this can be built — e.g. this device queries the backend for "the truck's current in-progress route" and caches it, or `cv-argus`/the Pi gets extended to carry route context. Not solved anywhere yet. |
| `severity_level` (`critical`/`medium`/`low`) | thresholds on `payload.probabilities[1]` (`p(Drowsy)`), fused with the local grip reading | ✅ **recommended, not undefined anymore** — see section 5's fusion matrix below. Short version: `p(Drowsy) >= 0.57` (matches `src/cv-argus`'s own `FUSED_MODEL_THRESHOLD` decision boundary) counts as the fusion matrix's `Drowsy` state, `< 0.57` as `Not Drowsy`; combined with `good`/`bad` grip per the matrix, never derived from `p(Drowsy)` alone. |
| `source` (`fusion`/`panic_button`) | which decision path fired | ✅ `"fusion"` for every drowsiness+grip-evaluated alert (section 5); `"panic_button"` for a direct panic-button press (section 5, unconditional `critical`, no debounce) |
| `grip_status` (`good`/`bad`) | your own grip-sensor reading at the moment of the fusion decision | ✅ required (and only meaningful) when `source == "fusion"`; `null` for `panic_button` |
| `related_alert_id` | the original alert's `id_alert`, when this POST is an escalation | ✅ set only when escalating a still-open incident (section 5) — omit/`null` for a fresh alert |
| `alert_type` | `payload.class_name`, probably | reasonable default, not formally decided |
| `ai_metadata.model`, `.clip_seconds` | — | these two sub-fields are nullable *within* `ai_metadata` (cv-argus doesn't produce either) — not the same as `ai_metadata` itself being optional, which depends on `source` (above) |

| Backend field (`StatusRouteCreate`) | Source | Status |
|---|---|---|
| `current_coordinates` | your own GPS reading | ✅ direct |
| `vigilance` (`critical`/`medium`/`low`) | mirrors the `severity_level` most recently posted to `POST /api/alerts` for this truck's current route | ✅ **recommended, not undefined anymore** — same `Severity` scale as `Alert.severity_level` now (backend-argus's severity-taxonomy unification), so no separate mapping table is needed; default to `low` if no alert has been posted yet this route. |
| `current_speed`, `odometer` | — | ❓ **undefined.** `cv-argus`'s `route_status` payload is only `{"status": "OK"}` — no speed/odometer data at all. This device needs its own source for these (CAN bus/OBD reading), not cv-argus. |

## 3. Geolocation — this device's responsibility, not cv-argus's

`cv-argus` never sets `Alert.geolocation` — the Pi has no GPS in this design (see the diagram:
the geolocation module is wired directly to the ESP32, not the Pi). Every record you relay
needs your own live GPS reading attached before the HTTP call, not copied from anything cv-argus
sent you.

## 4. Hardware ownership — entirely new work, no existing code to build on

Per the root `CLAUDE.md` and `semantic-design.drawio.xml`, this device owns all of the
following, and **none of them have any code anywhere in this repo yet**:

- **Grip sensor** (steering wheel) — feeds this device's own fusion decision loop; see section 5
  for the full spec (exact GPIO signal/debounce hardware details still not defined, but the
  decision algorithm itself now is).
- **Panic button** — triggers an alert directly; not routed through `cv-argus`'s orchestrator at
  all (that module's own docs say so explicitly), and bypasses the fusion loop entirely (section
  5) — unconditional `critical`, no debounce.
- **CAN bus / AEB actuator** — preventive autonomous braking.
- **Alarm speaker** — in-cabin audible alert.

None of these have a defined GPIO pin mapping or firmware interaction pattern yet — this is
genuinely from-scratch hardware-integration work. The grip sensor's *decision* algorithm (what to
do with its reading once you have one) is now specified in section 5, which is the part that
doesn't depend on which physical sensor/GPIO pin ends up producing that reading.

## 5. Grip sensor + drowsiness fusion — this device's own decision loop

This is the fused severity decision this device computes from two inputs it alone has both of:
the Pi's relayed drowsiness classification (section 1) and its own live grip-sensor reading.
Modeled explicitly on `cv-argus`'s own `orchestrator/orchestrator.py` debounce/cooldown design —
same vocabulary (debounce, escalation, recovery), extended to a joint two-signal state instead of
the camera alone. A typed, executable, **now fully implemented and unit-tested** reference for
everything below lives in `src/cv-argus/src/orchestrator/fusion_contract.py`'s
`FusionOrchestrator` (still not wired into the Pi's running pipeline — a contract reference, not
part of `cv-argus`'s own decision loop) — translate its state machine into firmware 1:1 rather
than re-deriving it from the matrix/windows alone below.

**Base severity matrix** — both signals bad → `critical`, both good → `low`, either one alone bad
→ `medium`:

| | Good grip | Bad/low grip |
|---|---|---|
| **Not Drowsy** (`p(Drowsy) < 0.57`) | `low` | `medium` |
| **Drowsy** (`p(Drowsy) >= 0.57`) | `medium` | `critical` |

**Three timing windows** (proposed defaults, same order of magnitude as `cv-argus`'s own 30s
`ORCHESTRATOR_ALERT_COOLDOWN_SECONDS` — not tuned against a real drive yet, tune deliberately once
one exists):

- **Debounce** (~5s) — the combined `(drowsy, grip)` state must hold this long before it first
  triggers a `POST /api/alerts` at that severity, so one noisy frame or a momentary
  hand-off-wheel gear shift doesn't fire an alert. **This same, short window also confirms a
  sudden worsening of an already-open incident** — e.g. an open `medium` incident where the very
  next reading maps straight to `critical` (both signals suddenly bad) confirms after this
  debounce window, *not* the much longer escalation window below. These are different real
  situations (a sudden worsening vs. a `medium` state quietly lingering unresolved) and treating
  them with the same slow timer would either let a genuinely fast-developing critical situation
  go unflagged for 20s, or make the slow-escalation timer meaningless. See
  `fusion_contract.py`'s `FusionOrchestrator.update()` docstring for the exact branch.
- **Escalation** (~20s) — an open `medium` incident that hasn't returned to `low` within this
  window escalates to `critical`: **`POST /api/alerts` again**, a new row with `related_alert_id`
  set to the original `medium` alert's `id_alert` — never a `PUT` mutating the original's
  `severity_level` in place, since `Alert` is an append-only event record on the backend. This
  20s figure was reconsidered and deliberately kept, not left as an unexamined guess: a shorter
  window was considered (it would shrink how long a genuinely dangerous `Drowsy + good grip`
  case can go before being flagged critical) but rejected because `medium` also fires for
  `Not Drowsy + bad grip` — a fully alert driver with a hand off the wheel (radio, coffee),
  common and usually harmless. One shared timer serves both causes; a faster one would routinely
  escalate the benign case too, training drivers to ignore alerts. See `fusion_contract.py`'s
  `FusionConfig` docstring for the same reasoning and the split-timer alternative it also
  considered and didn't adopt.
- **No automatic de-escalation.** Only a full recovery (both signals good, held the recovery
  window below) clears an open incident — a *partial* improvement (e.g. `critical` → `medium`)
  never steps the alert's severity back down on its own; it only resets the recovery clock, since
  the reading isn't fully good yet. A driver who improves from `critical` to `medium` and stays
  there keeps showing `critical` until they either fully recover or worsen again. A **manual**
  override is the intended path for correcting a false-positive-turned-benign situation — a
  guardian who has actually talked to the driver and confirmed they're fine, stepping the alert
  back down themselves through the dashboard. That's a `backend-argus`/`ui-argus` feature, not
  this device's job, and not built yet — tracked in `docs/roadmap.md`.
- **Recovery** (~10s) — once both signals return to good and hold for this long, mark the open
  incident resolved: `PUT /api/alerts/{id}` with `{"resolved_at": "<now, ISO 8601>"}`, using the
  device API key (this device may set `resolved_at` this way, but not
  `reviewed_by_operator`/`operator_notes` — those are human-review-only, see
  `src/backend-argus/CLAUDE.md`'s "Device (ESP32) auth"). **Only the latest/most-severe row in
  the incident gets this `PUT`** — if the incident escalated, that's the `critical` row, and the
  original `medium` row's own `resolved_at` stays `null` forever. One `PUT` call on recovery, not
  one per row in the chain; the dashboard needs to treat the original row as closed via its
  `related_alert_id` link to the resolved row, not by querying `resolved_at` on it directly.

**Panic button bypasses all of the above** — a press is always `POST /api/alerts` with
`source: "panic_button"`, `severity_level: "critical"`, `ai_metadata`/`grip_status` both
omitted/`null`, immediately, with no debounce and no interaction with any open fusion incident.

## Why Bluetooth, not the CAN bus/UART, for the Pi link

Already a settled decision, not open: Bluetooth SPP, ESP32-as-client/polling side — see the root
`CLAUDE.md`'s edge-side section and `cv-argus/CLAUDE.md`'s "Open decisions" for the reasoning
(container-wise this means passing through `/dev/rfcommN` or the host's BlueZ/D-Bus socket, not
a UART device node).
