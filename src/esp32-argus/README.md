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

`cv-argus`'s `sender/` module (on branch `worktree-cv-argus-alert-pipeline` — not merged yet,
but fully implemented and unit-tested against a fake transport) runs a Bluetooth SPP **server**
on the Pi. This device is the **client**: it connects, and initiates every exchange — the Pi
never pushes. Exact grammar (`src/cv-argus/src/sender/protocol.py`):

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

`backend-argus` (branch `worktree-backend-argus` — also not merged yet, but real, tested code)
already has endpoints built and auth designed specifically anticipating this device as the
caller — see `src/backend-argus/CLAUDE.md`'s "Device (ESP32) auth" section for the full
rationale. What you need to do:

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
| `ai_metadata.scores.not_drowsy`/`.drowsy` | `payload.probabilities[0]`/`[1]` | ✅ direct |
| `coordinates`, `speed_at_event` | your own GPS/speed reading | ✅ direct (this device's job — `geolocation` is always `null` from cv-argus, see section 3) |
| `id_route` | — | ❓ **undefined.** `cv-argus`'s `Alert.source_id` is a camera/stream id, not a route id. Nothing today tells this device which `Route` document a given truck's alert belongs to. Needs a real answer before this can be built — e.g. this device queries the backend for "the truck's current in-progress route" and caches it, or `cv-argus`/the Pi gets extended to carry route context. Not solved anywhere yet. |
| `severity_level` (`critical`/`medium`/`low`) | — | ❓ **undefined.** `cv-argus` only produces a binary `level` (1/2) and raw probabilities, not a 3-way severity. Needs a threshold/mapping decision. |
| `alert_type` | `payload.class_name`, probably | reasonable default, not formally decided |
| `ai_metadata.model`, `.clip_seconds` | — | already nullable on the backend precisely because cv-argus doesn't produce these — safe to omit |

| Backend field (`StatusRouteCreate`) | Source | Status |
|---|---|---|
| `current_coordinates` | your own GPS reading | ✅ direct |
| `vigilance` | — | ❓ likely derived from the truck's most recent `drowsiness` alert level, not from the `route_status` record itself (which carries no drowsiness info) — undefined |
| `current_speed`, `odometer` | — | ❓ **undefined.** `cv-argus`'s `route_status` payload is only `{"status": "OK"}` — no speed/odometer data at all. This device needs its own source for these (CAN bus/OBD reading), not cv-argus. |

## 3. Geolocation — this device's responsibility, not cv-argus's

`cv-argus` never sets `Alert.geolocation` — the Pi has no GPS in this design (see the diagram:
the geolocation module is wired directly to the ESP32, not the Pi). Every record you relay
needs your own live GPS reading attached before the HTTP call, not copied from anything cv-argus
sent you.

## 4. Hardware ownership — entirely new work, no existing code to build on

Per the root `CLAUDE.md` and `semantic-design.drawio.xml`, this device owns all of the
following, and **none of them have any code anywhere in this repo yet**:

- **Grip sensor** (steering wheel) — feeds the decision-making loop; exact signal/threshold not
  defined.
- **Panic button** — triggers an alert directly; not routed through `cv-argus`'s orchestrator at
  all (that module's own docs say so explicitly).
- **CAN bus / AEB actuator** — preventive autonomous braking.
- **Alarm speaker** — in-cabin audible alert.

None of these have a defined signal format, GPIO pin mapping, or firmware interaction pattern
yet — this is genuinely from-scratch hardware-integration work.

## Why Bluetooth, not the CAN bus/UART, for the Pi link

Already a settled decision, not open: Bluetooth SPP, ESP32-as-client/polling side — see the root
`CLAUDE.md`'s edge-side section and `cv-argus/CLAUDE.md`'s "Open decisions" for the reasoning
(container-wise this means passing through `/dev/rfcommN` or the host's BlueZ/D-Bus socket, not
a UART device node).
