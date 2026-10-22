# esp32-argus

Per the root `CLAUDE.md`, this is the **"Message Sender Orchestrator"**: the device that owns
everything actuation-/safety-critical in the truck cabin — the alarm speaker, the CAN bus/AEB
actuator, the panic button, the geolocation (GPS) module, and the steering-wheel grip sensor —
and is the only device that talks to the cloud backend. It has two jobs: pull queued
alert/status records off the Raspberry Pi over Bluetooth, and relay them to the backend over
HTTP with its own GPS reading attached.

## 0. Status

Real firmware now exists, as an **Arduino IDE multi-tab sketch** under
`src/esp32_argus/esp32_argus.ino` plus paired `.h`/`.cpp` files per concern — written and
reviewed by hand, but **not yet compiled or run against real hardware**: no Arduino toolchain
or physical ESP32/SIM800L/NEO-6M harness has been available in any session that's touched this
repo. Treat it the same way `cv-argus`'s own Bluetooth server is treated in that module's
`CLAUDE.md` — a real implementation of the protocol, not a tested link. The rest of this file
(sections 1-6) is still the authoritative contract spec; what follows documents what the
firmware actually does with it.

**Files:**

| File | Responsibility |
|---|---|
| `esp32_argus.ino` | `setup()`/`loop()` — wires every module together; `handle()` (logs a pulled record, decides, composes, sends or reports `FAIL`), the dead-letter replay, and `handlePanicButton()` live here |
| `config.h` | every pin and timing constant, plus the Pi's Bluetooth MAC — nothing per-truck or per-network; see "Before you flash this" below |
| `device_config.*` | pure parser for the Pi's `CONFIG` reply (backend URL, device API key, truck id) — host-tested in `test/host/device_config_host_test.cpp` |
| `credentials.*` | the credentials currently in use, cached in NVS; re-fetched from the Pi at every boot and after any 401 (see section 1b) |
| `root_ca.h` | Amazon Root CA 1, pinned for `https://` backends (the AWS CloudFront deployment) |
| `gps_module.*` | NEO-6M over `TinyGPSPlus` — lat/lon/speed/satellite count, plus a GPS-derived UTC timestamp (`gpsUtcIso8601()`) used for `Alert.resolved_at`, so this device never needs an NTP-synced clock |
| `sms_module.*` | panic SMS through the SIM800L — the panic button's hardware-direct fallback, which still goes out when the backend is unreachable |
| `modem.*` | the SIM800L's UART + raw AT helpers (no TinyGsm — see `sms_module.cpp`), shared by SMS and mobile data |
| `gprs_http.*` | HTTPS GET/POST over the SIM800L's mobile data (`AT+SAPBR` bearer, `AT+HTTP*` with `AT+HTTPSSL=1`) — the **first-choice** transport, see the network-path decision below |
| `modem_parse.*` / `transport_policy.*` / `ack_policy.*` | pure, host-tested logic: SIM800L response parsers; GPRS-first/WiFi-fallback selection; what to do with a pulled record per HTTP status (200/201 ack, 400/409/422 ack-and-drop, 401/403/404/5xx/transport errors hold) |
| `wifi_client.*` | the **fallback** transport (a phone hotspot or 4G MiFi works), with non-blocking reconnect |
| `backend_api.*` | `backendReadiness()` (credentials, network, active route via `GET /api/devices/me/active-route`, each a `FAIL` reason) and `backendPost()` (logs the request and the answer); picks the transport, and on WiFi verifies an `https://` backend against the pinned CA (`root_ca.h`) |
| `backend_messages.*` | pure, host-tested: composes every backend body (alert, resolve, status, panic) apart from sending it, so it can be logged and handed back to the Pi; fills the route and a missing position at send time |
| `pi_record.*` / `pi_event_policy.*` | pure, host-tested: parse one Pi record (unreadable ones are dropped but ACKable); decide alert / escalation / resolve / status / nothing from the Pi's severity and episode |
| `bluetooth_client.*` | `bluetoothExchange()`: one `PULL`/`DLQ` exchange with the `FAIL`/`ACK`/`DONE` report (section 1) via ESP32's built-in classic Bluetooth (`BluetoothSerial`) — a separate radio from WiFi, both run concurrently |
| `fusion.*` | `FusionOrchestrator` — a 1:1 C++ port of `cv-argus`'s `fusion_contract.py` (section 5); **not used while there's no grip sensor** |
| `panic_button.*` / `grip_sensor.*` | debounced digital-input wrappers; GPIO pins are placeholders (see `config.h`) — real hardware mapping isn't finalized |

`src/Codigo_SMS_GPS/Codigo_SMS_GPS.ino` is the original standalone SIM800L + NEO-6M
prototype this firmware grew out of: it waits for a GPS fix and sends one emergency SMS with a
Google Maps link. It isn't part of the deployed firmware. Keep it as a **hardware bring-up
sketch**: flash it first on a new harness to confirm the modem, SIM, and GPS wiring work
before debugging the full multi-module firmware. Its pins and destination number match
`config.h`.

**Network-path decision (user decision, replacing the earlier WiFi-only one):** mobile data
over the SIM800L's GPRS is the first-choice transport for every `GET`/`POST`, and WiFi (e.g. a phone
hotspot or a 4G MiFi in the cab) is the fallback. Only a *transport* failure (no registration,
no bearer, a SIM800L 6xx code) switches to WiFi, for `GPRS_HOLD_OFF_MS`, before mobile data is
tried again; a server answer, even a 4xx/5xx, never does (`transport_policy.h`). `PUT
/api/alerts/{id}` (resolving an incident) is WiFi-only, because the SIM800L's `AT+HTTPACTION`
has no PUT. Both networks come from the Pi, not the firmware: `ARGUS_WIFI_SSID`/
`ARGUS_WIFI_PASSWORD` and `ARGUS_GPRS_APN`/`ARGUS_GPRS_USER`/`ARGUS_GPRS_PASSWORD` in its `.env`,
sent inside the `CONFIG` reply (section 1b). No APN = WiFi only (right for the bench backend on
a LAN IP, which the mobile network can't reach).

**Reaching the AWS deployment** (`src/infra-argus`): the backend URL the Pi hands over
(`ARGUS_BACKEND_URL`, section 1b) is the CloudFront URL and must be `https://`. CloudFront's
`/api/*` behavior is HTTPS-only and answers **403** to plain HTTP. Over WiFi the ESP32 does TLS
itself and checks the chain against `root_ca.h` (Amazon Root CA 1). Over GPRS the TLS runs on the SIM800L, whose SSL is TLS 1.0 without SNI; CloudFront's
default `*.cloudfront.net` certificate accepts exactly that (checked with `openssl s_client
-tls1 -noservername -cipher AES128-SHA` against the live distribution). Two consequences: the
modem doesn't verify the certificate (encrypted, not authenticated), and moving the API to a
custom domain with a TLS 1.2-only policy would break the GPRS path. GPRS also needs a SIM with a
data plan and 2G coverage, and the carrier's APN in the Pi's `.env` (see
`src/cv-argus/.env.example` for Telcel, AT&T and Movistar values).

The SIM800L still sends the panic **SMS**, and SMS fires **only** from the panic button
(`handlePanicButton()` in the main sketch) — a redundant, hardware-direct channel alongside the
normal `POST /api/alerts`, so an emergency location text still goes out even if
`backend-argus` itself is down. It does *not* fire for any fused drowsy+grip severity.

**Before you flash this**, the only per-site value in `config.h` is `PI_BT_MAC_ADDRESS` (the
Pi's Bluetooth MAC: the "Controller" line of `bluetoothctl show` on the Pi, not its WiFi MAC —
this device needs it to reach the Pi at all). `PI_BT_CHANNEL` (4) must match cv-argus's
`BLUETOOTH_CHANNEL`. At boot this device prints its own Bluetooth MAC
(`[bt] This ESP32's Bluetooth MAC: …`), which goes in the Pi's `.env` as `ESP32_BT_MAC`. **Nothing per-truck or per-network is compiled in**: the backend URL, device API key, WiFi
network and mobile-data APN come from the Pi over Bluetooth (section 1b), and the route from
the backend (section 2), so one firmware image fits every truck. Required Arduino libraries (Library
Manager names): `TinyGPSPlus`, `ArduinoJson` (v7+ — this code uses v7's `JsonDocument`
API), plus `BluetoothSerial`/`WiFi`/`HTTPClient`, which ship with the ESP32 board package
itself (no `TinyGSM` — `sms_module.cpp` talks to the SIM800L with raw AT commands instead, see
that file). Bluetooth must be enabled in the board's partition/SDK config (the default for most
standard ESP32 board variants) or `BluetoothSerial.h` won't compile.

**If the compiler reports the sketch is over 100% of program storage space**, check
**Tools → Partition Scheme** before cutting any more code: most ESP32 boards default to a
dual-OTA partition layout that only gives the app half of the chip's flash. Switching to a
single-app, no-OTA scheme (e.g. "Huge APP (3MB No OTA/1MB SPIFFS)" — the exact label depends on
your board package version) roughly doubles the space available with zero code changes, and
this firmware never uses OTA updates. After that, the next-biggest line item is
`BluetoothSerial` — the ESP32's classic-Bluetooth (SPP) stack is large by itself; there's no way
around that without dropping the Bluetooth-SPP pull from `cv-argus` entirely, which is a protocol
decision (see "Why Bluetooth" below), not a code-trim.

**Tests** live in `test/`: `test/host/` compiles the fusion state machine on the PC
(`test/host/run.sh`, needs only `g++`), and `test/hil/` is the bench test against the real,
flashed ESP32 (the PC plays the Pi over Bluetooth and the backend over WiFi; see its README.md).

## 1. Bluetooth SPP client — pulling from `cv-argus`

`cv-argus`'s `sender/` module runs a Bluetooth SPP **server** on the Pi (through the host's
BlueZ, see `src/cv-argus/README.md`). This device is the **client**: it connects, and initiates
every exchange — the Pi never pushes. Exact grammar (`src/cv-argus/src/sender/protocol.py`):

```
you  -> Pi:   PULL <n>\n  |  DLQ <k>\n       (live records | dead letters, oldest first)
Pi   -> you:  one compact JSON line per record (a dead letter also has "dlq": {...})
Pi   -> you:  END <dead letters waiting>\n   (sent even if 0 rows matched)
you  -> Pi:   FAIL <id> <reason> <json|->\n   (0..n: couldn't deliver it)
you  -> Pi:   ACK <id1>,<id2>,...\n           (delivered, or nothing to send)
Pi   -> you:  ACKED <n>\n
you  -> Pi:   DONE\n
Pi   -> you:  OK acked=<a> failed=<f> dlq=<n>\n
```

**Live first, dead-letter on failure** (user decision, 2026-10-20). Every poll (2 s) pulls the
live records, logs each one, and reports it back: `ACK` once the backend stored it (or when
there's nothing to send, like a keepalive), `FAIL` with a one-token reason (`no_credentials`,
`no_network`, `no_route`, `no_gps`, `no_time`, `http_<status>`) and the backend message
composed here, which the Pi keeps in its **dead-letter queue** with that message's GPS position
and time. Missing credentials never stop the pulling. When the backend is reachable and the Pi
said `END n` with n > 0, the same poll sends `DLQ 2` and replays them (stopping at the first
failure), reusing each stored message so its original position and time are kept. A record the
firmware can't parse (`pi_record.cpp`: a bare NaN token, an unknown kind, missing
probabilities) is ACKed away when its id is known, so it can't block the Pi's queue. Replayed
messages arrive at the backend late; how the backend and UI should treat them ("missing
alerts") is still to be planned (root `CLAUDE.md`).

**The JSON record shape** (`cv-argus/src/alerts/models.py`'s `Alert` dataclass):

```json
{
  "id": "a1b2c3d4...",             // uuid4 hex — client_event_id for the backend
  "kind": "drowsiness",            // or "route_status"
  "level": 2,                      // 1=Not Drowsy / 2=Drowsy, null for route_status / No Face
  "created_at_ms": 1234567890123,  // epoch ms, wall clock; the event time when clock_synced
  "mono_ms": 5000123456, "boot_id": "...", "clock_synced": true,
  "source_id": "cam-0",            // camera/stream id — NOT a route id
  "payload": { "...": "..." },     // shape depends on kind, see below
  "geolocation": null               // ALWAYS null coming from cv-argus — see section 3
}
```

`payload` by `kind`:
- `"drowsiness"` → `class_name` (`Drowsy`/`Not Drowsy`/`No Face`), `probabilities`
  (`[p_not_drowsy, p_drowsy]`, null only for `No Face`), `transition`, and the rules' verdict:
  `severity` (`none`/`low`/`medium`/`critical`), `previous_severity`, `reason` (e.g.
  `eyes_closed`), `episode`, `model`.
- `"route_status"` → `{"status": "OK"}`, the heartbeat; the position and vigilance come from
  this device.

**What it sends** (no grip sensor yet, so the Pi's severity decides; `pi_event_policy.cpp`):
a rise to medium/critical → `POST /api/alerts` (severity as is, `alert_type` = the reason,
`grip_status: "good"` as a placeholder, an escalation links to the episode's earlier alert);
the fall back to none/low → `POST /api/alerts/{id}/resolve`; a heartbeat → `POST
/api/routes/{id}/status` with the current position and the camera's severity as vigilance (skipped
without a GPS fix). Keepalives send nothing. `fusion.*` (camera + grip) stays for when the
sensor is wired.

**The panic button** is caught by its interrupt (`panic_latch.*`), so a press during a long
Bluetooth or HTTP call isn't missed. It sends the SMS at once, then queues the alert in NVS
(with the position and GPS time of the press and its own `client_event_id`) and retries it
every poll until the backend stores it, across reboots too. The backend alert id of each open
episode is kept in NVS as well, so a reboot mid-incident can still resolve it.

**What the serial log shows** (one line per record, keepalives included):
```
[pi] critical (was none) "eyes_closed" class=Drowsy p=[0.05,0.95] episode=3f2a | gps 20.659700,-103.349600 0 km/h
!!! CRITICAL from the camera: eyes_closed
[backend] alert: {"alert_type":"eyes_closed","severity_level":"critical",...}
[backend] Not sent: no_credentials (no argus-device.env on the Pi yet) -> the Pi keeps it
```

**How it connects.** `SerialBT.connect(PI_BT_MAC_ADDRESS, PI_BT_CHANNEL)`: straight to
RFCOMM channel 4 instead of an SDP lookup on every poll. A Windows PC playing the Pi
(`test/hil/`) needs `0`. Every exchange is a fresh connection; `[bt] CONNECTED to the Pi` /
`[bt] Can't reach the Pi` print only when that changes, and a link summary every 30 s.

## 1b. Credentials — fetched from the Pi, not flashed in

A truck is provisioned from the dashboard: **Fleet → truck → "Generate device credentials"**
(root_admin/admin) issues a device key (`POST /api/trucks/{id}/rotate-key`, shown once) and
downloads an `argus-device.env` (`ARGUS_TRUCK_ID`, `ARGUS_DEVICE_API_KEY`, `ARGUS_BACKEND_URL`).
That file goes next to `cv-argus`'s `docker-compose.yml` on the truck's Pi. The Pi then serves
it to this device over the same Bluetooth link, as one more request in the protocol above:

```
you  -> Pi:   CONFIG\n
Pi   -> you:  CONFIG {"truck_id":"...","device_api_key":"...","backend_url":"https://...",
                      "wifi_ssid":"...","wifi_password":"...","gprs_apn":"...",
                      "gprs_user":"...","gprs_password":"..."}\n
              or NOCONFIG\n                 (no argus-device.env on the Pi yet)
```

The five network fields are optional and come from the Pi's own `.env` (`ARGUS_WIFI_SSID`,
`ARGUS_WIFI_PASSWORD`, `ARGUS_GPRS_APN`, `ARGUS_GPRS_USER`, `ARGUS_GPRS_PASSWORD`), not from
`argus-device.env`, which the dashboard regenerates on every key rotation. Absent WiFi = WiFi
stays off; absent APN = no mobile data. Values are used verbatim (an SSID may end in a space).
A new SSID makes `wifi_client.cpp` reconnect; a new APN makes `gprs_http.cpp` reopen the bearer.

When this device asks (`refreshCredentialsIfStale()` in the main sketch, before a pull):
- **every boot**, so a key rotated in the dashboard reaches the truck after the Pi restarts;
- **after any `401`** from the backend (a rotated or revoked key);
- every `CONFIG_RETRY_INTERVAL_MS` while the Pi answers `NOCONFIG`;
- every `CONFIG_REFRESH_INTERVAL_MS` (10 min) otherwise, so a changed `.env` on the Pi (new
  WiFi, new APN) arrives without a reboot.

The last good copy is cached in NVS (`credentials.cpp`), so a truck that boots before its Pi
still reaches the backend. No credentials at all → this device doesn't pull, and records stay
queued on the Pi. The key and the WiFi/APN passwords never go to `Serial`.

**Link security**: the Pi requires a paired, encrypted RFCOMM link (`cv-argus`'s
`BLUETOOTH_SECURITY=medium`), because this reply carries the key. This side pairs automatically
(SSP "Just Works": no display or keypad). On the Pi, cv-argus's own pairing agent accepts only
the MAC in `ESP32_BT_MAC`, so no `bluetoothctl` step is needed (`src/cv-argus/README.md`,
"Provisioning a truck"). Just Works gives encryption but no MITM protection during pairing,
which is the known limit of a display-less device. This device connects straight to
`PI_BT_CHANNEL` rather than looking the channel up (SDP) on every poll.

## 2. HTTP relay to `backend-argus`

`backend-argus` (merged to `main` — real, tested code) already has endpoints built and auth
designed specifically anticipating this device as the caller — see
`src/backend-argus/CLAUDE.md`'s "Device (ESP32) auth" section for the full rationale. What you
need to do:

- **Auth**: send `X-Device-Api-Key: <key>` on every request. The key is per-truck, issued from
  the dashboard and handed over by the Pi (section 1b). It's checked against the specific truck
  that owns the route you're posting to — a stolen key only works for its own truck's data.
- **Transport**: `https://` backend URLs (the AWS deployment: CloudFront only serves `/api/*`
  over HTTPS) use `WiFiClientSecure` with Amazon Root CA 1 pinned (`root_ca.h`); `http://`
  (a LAN bench backend) uses plain `WiFiClient`. Timeouts are `BACKEND_HTTP_TIMEOUT_MS` (15 s)
  to ride out Lambda cold starts.
- **`kind: "drowsiness"` records** → `POST /api/alerts`
- **`kind: "route_status"` records** → `POST /api/routes/{id}/status`

**Field mapping — what's a direct translation vs. genuinely undefined (be honest about this,
don't guess a wrong answer into firmware code):**

| Backend field (`AlertCreate`) | Source | Status |
|---|---|---|
| `ai_metadata.scores.not_drowsy`/`.drowsy` | `payload.probabilities[0]`/`[1]` | ✅ direct — required (and only meaningful) when `source == "fusion"`; the whole `ai_metadata` object is omitted/`null` for a `panic_button` alert, not just its sub-fields |
| `coordinates`, `speed_at_event` | your own GPS/speed reading | ✅ direct (this device's job — `geolocation` is always `null` from cv-argus, see section 3) |
| `id_route` | `GET /api/devices/me/active-route` (the backend resolves the truck from the key) | ✅ **solved.** Looked up on the first write, cached, and dropped on a `404`/`409` (route ended) or a `401`; retried at most every `ACTIVE_ROUTE_RETRY_MS` while the truck has no active route. Until it has one, writes aren't sent and records stay unacked on the Pi. `cv-argus`'s `Alert.source_id` is a camera id, never a route id. |
| `severity_level` (`critical`/`medium`/`low`) | thresholds on `payload.probabilities[1]` (`p(Drowsy)`), fused with the local grip reading | ✅ **recommended, not undefined anymore** — see section 5's fusion matrix below. Short version: `p(Drowsy) >= 0.57` (cv-argus's `RULE_P_DROWSY` sends 0.75/0.95 for MEDIUM/CRITICAL severity and 0.30 for LOW, so only MEDIUM+ crosses it) counts as the fusion matrix's `Drowsy` state, `< 0.57` as `Not Drowsy`; combined with `good`/`bad` grip per the matrix, never derived from `p(Drowsy)` alone. |
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

## 4. Hardware ownership

Per the root `CLAUDE.md` and `semantic-design.drawio.xml`, this device owns all of the
following:

- **Grip sensor** (steering wheel) — feeds this device's own fusion decision loop; see section 5
  for the full spec. `grip_sensor.h`/`.cpp` now exist (a debounced digital-input wrapper on
  `GRIP_SENSOR_PIN`), but the GPIO pin/signal convention is a **placeholder**, not a verified
  mapping against a real sensor — see `config.h`'s comment on it.
- **Panic button** — triggers an alert directly; not routed through `cv-argus`'s orchestrator at
  all (that module's own docs say so explicitly), and bypasses the fusion loop entirely (section
  5) — unconditional `critical`, no debounce. `panic_button.h`/`.cpp` now implement the
  debounce/edge-detection; same placeholder-GPIO caveat as the grip sensor.
- **CAN bus / AEB actuator** — preventive autonomous braking. **Still genuinely no code
  anywhere in this repo** — out of scope for this pass.
- **Alarm speaker** — in-cabin audible alert. **Still genuinely no code anywhere in this repo**
  — out of scope for this pass.

The grip sensor and panic button have real debounce/read logic now, but neither has a
hardware-verified GPIO pin mapping — `config.h` flags both placeholders explicitly and explains
why GPIO 34-39 were avoided (no internal pull-up on that pin group). The CAN bus/AEB actuator and
alarm speaker remain entirely unimplemented, same as before.

## 5. Grip sensor + drowsiness fusion — this device's own decision loop

> **Not in use for now** (user decision, 2026-10-20): the grip sensor isn't wired, so alerts
> follow the Pi's own severity (section 1, "What it sends") and resolving uses `POST
> /api/alerts/{id}/resolve`. This section is the design for when the sensor is added.

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
- **Failed POSTs are retried, never dropped.** An alert stays owed until `recordAlertPosted()`
  confirms it, and `update()` raises it again on every later tick until then, even after the
  driver starts recovering. A newer raise (an escalation) replaces an older owed one. Without
  this, a POST that failed while WiFi or the backend was down would never be sent again,
  because the state machine had already confirmed that severity, and the next pulled record
  would be ACKed with the alert lost. Caught by `src/tests/edge_chain/`. The C++ port has
  host-compiled tests: `test/host/run.sh` (needs only `g++`).

**Panic button bypasses all of the above** — a press is always `POST /api/alerts` with
`source: "panic_button"`, `severity_level: "critical"`, `ai_metadata`/`grip_status` both
omitted/`null`, immediately, with no debounce and no interaction with any open fusion incident.

## Why Bluetooth, not the CAN bus/UART, for the Pi link

Already a settled decision, not open: Bluetooth SPP, ESP32-as-client/polling side — see the root
`CLAUDE.md`'s edge-side section and `cv-argus/CLAUDE.md`'s "Open decisions" for the reasoning
(container-wise this means passing through `/dev/rfcommN` or the host's BlueZ/D-Bus socket, not
a UART device node).
