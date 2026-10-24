# esp32-argus

Per the root `CLAUDE.md`, this is the **"Message Sender Orchestrator"**: the device in the truck
cabin that owns the hardware the Pi doesn't (the GPS module, the SIM800L modem, later the
steering-wheel grip sensor, the alarm speaker and the CAN bus/AEB actuator) and is the only
device that talks to the cloud backend. It has two jobs: pull the camera's records off the
Raspberry Pi over Bluetooth, and send the backend one combined **report** at a time (the GPS
points since the last report plus at most one event), with its own GPS position attached.

The contracts it implements are `docs/designs/final-tweaks-contracts.md` **C1** (Pi ↔ ESP32
Bluetooth protocol v2) and **C2** (`POST /api/devices/me/report`). Where this file and that one
differ, the contracts file wins.

## 0. Status

Real firmware: an **Arduino IDE multi-tab sketch**, `src/esp32_argus/esp32_argus.ino` plus a
`.h`/`.cpp` pair per concern.

- **The final-tweaks firmware (this one) compiles** for the real target with
  `test/compile.sh` (core 3.3.12, Huge APP, `--warnings all`; 2026-10-23): 1,825,248 bytes (58%
  of flash) and 84,404 bytes (25%) of RAM. The only warning from our files is core 3.x marking
  `BluetoothSerial` deprecated (it stays supported until core 4.0). It has **not been flashed or run on hardware yet**. Protocol v2
  with the real Pi, the GPRS path, the AWS/HTTPS path, the 1 s GPS reads and the heap/stack
  headroom of the three tasks are all still to be checked on the bench (`test/hil/`).
- **The earlier (v1) firmware** compiled, was flashed to a real ESP32-D0WD-V3, and passed
  `test/hil/` over WiFi against a bench backend (2026-10-17). v1 used the retired per-message
  endpoints and the panic button; none of that is in this firmware.
- **Bluetooth v2 is not compatible with v1**: flash this firmware and deploy the matching Pi
  image (`src/cv-argus` with protocol v2) together.

**Files:**

| File | Responsibility |
|---|---|
| `esp32_argus.ino` | `setup()`, the three tasks (`loop()`, `gpsTask`, `senderTask`, section 0b), the shared state behind `stateMutex`, the per-record and per-session log lines, the `[health]` line |
| `config.h` | every pin, timing and tuning constant (contracts C4), plus the Pi's Bluetooth MAC; nothing per-truck or per-network |
| `device_config.*` | pure parser for the Pi's `CONFIG` reply (backend URL, device API key, truck id, WiFi, APN); `sameBackendIdentity()` |
| `credentials.*` | the credentials in use, cached in NVS, re-fetched from the Pi at every boot, after a 401 and every 10 min (section 1b); thread-safe copies |
| `root_ca.h` | Amazon Root CA 1, pinned for `https://` backends over WiFi (the AWS CloudFront deployment) |
| `gps_module.*` | NEO-6M over `TinyGPSPlus`: position, speed, GPS UTC time, a `[gps]` status line; only `gpsTask` calls it |
| `gps_track.*` | pure: the track buffer (the points since the last report, newest `REPORT_MAX_POINTS` kept), the last known position (NVS blob, its age across reboots) and each point's `vigilance` |
| `modem.*` | the SIM800L's UART and raw AT helpers (no TinyGsm), with the attempt's deadline applied to every wait and a cached registration state |
| `gprs_http.*` | the report POST over the SIM800L's mobile data (`AT+SAPBR` bearer, `AT+HTTP*` with `AT+HTTPSSL=1`): the **first-choice** transport. Pre-emptible between AT steps, drains a late `+HTTPACTION` |
| `wifi_client.*` | the **fallback** transport (a phone hotspot or a 4G MiFi): radio off until mobile data fails, non-blocking reconnect |
| `backend_api.*` | `backendPostReport()`: one attempt with a real deadline; picks GPRS or WiFi, composes the body as late as possible (right before `AT+HTTPDATA` / `http.POST`), logs the request and the answer |
| `backend_messages.*` | pure: composes the report body and the event object (also the `<body>` of the FAIL that dead-letters a critical), parses the 200 answer |
| `ack_policy.*` | pure: what one report's answer means (C2.3): delivered / rejected / attempt failed, the active route, re-CONFIG on 401 |
| `transport_policy.*` | pure: GPRS first with a WiFi hold-off after a mobile transport failure; the attempt timeouts; the `Deadline` |
| `modem_parse.*` | pure: SIM800L response parsers; the late-`+HTTPACTION` verdict (`modem_busy`) |
| `sender_policy.*` | pure: what the sender sends next and what happens to each event after each attempt (priority, pre-emption, retry windows, DLQ one try, outcomes for the Pi), in static slots |
| `sender_step.*` | pure: one attempt minus the I/O (prepare under the mutex, compose at send time, finish under the mutex) and its `[live]`/`[dlq]` log line |
| `pi_record.*` / `pi_event_policy.*` | pure: parse one Pi record (unusable ones are ACKed away by id); the C1.5 event rule |
| `pi_protocol.*` / `pull_session.*` | pure: the v2 client grammar (`PULL`/`DLQ 1`, `END`, `ACK`/`TAKEN`/`FAIL`, `OK`); what to do with each served record |
| `bluetooth_client.*` | the Bluetooth I/O (`BluetoothSerial`): one fresh RFCOMM connection per session; reads the whole batch before handling it |
| `bounded_string.h` / `rtos_lock.h` | fixed-capacity strings for the static slots; a scoped FreeRTOS mutex lock |
| `fusion.*` | `FusionOrchestrator`, a 1:1 C++ port of `cv-argus`'s `fusion_contract.py` (section 5); **not used while there's no grip sensor** |
| `grip_sensor.*` | debounced digital input on a placeholder GPIO; not used yet |

`src/Codigo_SMS_GPS/Codigo_SMS_GPS.ino` is the original standalone SIM800L + NEO-6M prototype
this firmware grew out of. It isn't part of the deployed firmware (which sends no SMS). Keep it
as a **hardware bring-up sketch**: flash it first on a new harness to confirm the modem, SIM and
GPS wiring before debugging the full firmware. Its modem and GPS pins match `config.h`.

**Network-path decision (user decision):** mobile data over the SIM800L's GPRS is the
first-choice transport for the report POST, and WiFi (e.g. a phone hotspot or a 4G MiFi in the
cab) is the fallback. Only a *transport* failure (no registration, no bearer, a timeout, a
SIM800L 6xx code) switches to WiFi, for `GPRS_HOLD_OFF_MS` (5 min), before mobile data is tried
again; a server answer, even a 4xx/5xx, never does (`transport_policy.h`). A modem still busy
with an earlier timed-out request (`modem_busy`) is not a transport failure either. Both
networks come from the Pi, not the firmware: `ARGUS_WIFI_SSID`/`ARGUS_WIFI_PASSWORD` and
`ARGUS_GPRS_APN`/`ARGUS_GPRS_USER`/`ARGUS_GPRS_PASSWORD` in its `.env`, sent inside the `CONFIG`
reply (section 1b). No APN = WiFi only (right for the bench backend on a LAN IP, which the
mobile network can't reach). The report is a POST, which the SIM800L's `AT+HTTPACTION` supports,
so nothing is WiFi-only any more.

**Reaching the AWS deployment** (`src/infra-argus`): the backend URL the Pi hands over
(`ARGUS_BACKEND_URL`, section 1b) is the CloudFront URL and must be `https://`. CloudFront's
`/api/*` behavior is HTTPS-only and answers **403** to plain HTTP. Over WiFi the ESP32 does TLS
itself and checks the chain against `root_ca.h` (Amazon Root CA 1). Over GPRS the TLS runs on the
SIM800L, whose SSL is TLS 1.0 without SNI; CloudFront's default `*.cloudfront.net` certificate
accepts exactly that (checked with `openssl s_client -tls1 -noservername -cipher AES128-SHA`
against the live distribution). Two consequences: the modem doesn't verify the certificate
(encrypted, not authenticated), and moving the API to a custom domain with a TLS 1.2-only policy
would break the GPRS path. GPRS also needs a SIM with a data plan and 2G coverage, and the
carrier's APN in the Pi's `.env` (see `src/cv-argus/.env.example` for Telcel, AT&T and Movistar
values).

**Before you flash this**, the only per-site value in `config.h` is `PI_BT_MAC_ADDRESS` (the
Pi's Bluetooth MAC: the "Controller" line of `bluetoothctl show` on the Pi, not its WiFi MAC).
`PI_BT_CHANNEL` (4) must match cv-argus's `BLUETOOTH_CHANNEL`. At boot this device prints its
own Bluetooth MAC (`[bt] This ESP32's Bluetooth MAC: …`), which goes in the Pi's `.env` as
`ESP32_BT_MAC`. `BENCH_FAKE_GPS` must be `0` on a truck. **Nothing per-truck or per-network is
compiled in**: the backend URL, device API key, WiFi network and mobile-data APN come from the Pi
over Bluetooth (section 1b), and the active route from every report's answer (section 2), so one
firmware image fits every truck. Required Arduino libraries (Library Manager names):
`TinyGPSPlus`, `ArduinoJson` (v7+, this code uses v7's `JsonDocument` API), plus
`BluetoothSerial`/`WiFi`/`HTTPClient`/`Preferences`, which ship with the ESP32 board package. It
must be a classic ESP32 (Bluetooth Classic SPP), with the **Huge APP** partition scheme: WiFi +
Bluetooth Classic don't fit the default dual-OTA layout (`src/esp32_argus/CLAUDE.md` has the
toolchain details and the errors already hit).

**Tests** live in `test/`:
- `test/host/run.sh` compiles every pure module with `g++` and runs its host test on the PC (17
  suites: the C1 grammar and record handling, the C1.5 event rule, the sender's priority,
  pre-emption, retry windows and DLQ attempts, the report body and the answer's handling, the
  attempt deadlines, the last known GPS position, the modem parsers, fusion). Needs only `g++`.
- `test/vectors/` holds the vectors shared with other suites: `event_vectors.json` (C1.5; cv-argus
  and `src/tests/edge_chain` replay it too) and `fusion_vectors.json`.
- `test/compile.sh` builds the whole sketch for the real target with `arduino-cli` and prints our
  own warnings and the flash/RAM use. From WSL it can use the Windows `arduino-cli.exe` the
  Arduino IDE bundles (`ARDUINO_CLI=".../Arduino IDE/resources/app/lib/backend/resources/arduino-cli.exe"
  sh test/compile.sh`; about 3 min the first time). Flashing stays manual (Arduino IDE, COM3).
- `test/hil/` is the bench test against the real, flashed ESP32: the PC plays the Pi over
  Bluetooth and the backend over WiFi (see its README.md).
- `src/tests/edge_chain/` mirrors this firmware module for module in Python
  (`esp32_sim.py`) and runs it between cv-argus's real Pi server and backend-argus's real app.

## 0b. How the firmware is built: three tasks, one shared state

```
            Bluetooth (RFCOMM)                                  GPRS (SIM800L) / WiFi
 Pi ◀──── loop() ─────────────▶ ┌────────────── stateMutex ──────────────┐ ◀──── senderTask ────▶ backend
          PULL 8 / DLQ 1        │ SenderPolicy (live slots, DLQ slot,     │       POST /api/devices/me/report
          ACK / TAKEN / FAIL    │   outcomes for the Pi, known route)     │
                                │ TrackBuffer (points since last report)  │
 NEO-6M ──── gpsTask ─────────▶ │ VigilanceTracker, LastKnownPosition,    │
          a read every 1 s      │ the newest GPS read                     │
                                └─────────────────────────────────────────┘
```

- **`loop()`** (the Arduino loop task) talks to the Pi: every `PI_BT_POLL_INTERVAL_MS` (2 s) a
  `PULL 8` session, then `DLQ 1` when the Pi holds dead letters and no live event is pending
  (section 1). It fetches `CONFIG` when the credentials are stale. It never calls the backend:
  a session takes well under a second, far inside the Pi's 30 s wait for the report lines.
- **`gpsTask`** (priority 2, so its reads stay on time) drains the NEO-6M continuously and reads
  it every `GPS_READ_INTERVAL_MS` (1 s): one point per read into the track buffer (section 3).
  It saves the last known position to NVS.
- **`senderTask`** is **the only code that touches the modem, WiFi or HTTP**. One report per
  POST, by the rules below.

The three share one state behind **one FreeRTOS mutex, `stateMutex`**, held only for short,
I/O-free critical sections: no task holds it across a Bluetooth exchange, an NVS write or an
HTTP attempt. The sender copies what it needs under the lock (`prepareReport()`), sends with the
lock released, then applies the answer under the lock again (`finishReport()`); between AT
steps it takes the lock briefly to ask whether a higher event pre-empts it. Everything the
tasks share lives in **static slots** (fixed-capacity strings, `bounded_string.h`):
`SENDER_LIVE_SLOTS` (8) live events, one DLQ slot, `SENDER_OUTCOME_SLOTS` (16) outcomes for the
Pi, `REPORT_MAX_POINTS` (10) track points. A full live pool leaves a new event on the Pi (not
TAKEN; it is served again). Every busy-wait yields (`vTaskDelay(1)`), and a `std::bad_alloc` ends
the attempt or session as not made instead of rebooting.

**The sender's rules** (`sender_policy.h`, contracts C1.8/C2.3/C4):

- **When a report goes.** An event goes at once. Otherwise a points-only report goes
  `REPORT_INTERVAL_MS` (5 s) after the previous report *ended*; every `ACTIVE_ROUTE_RETRY_MS`
  (30 s) while the backend says the truck has no active route. With no position at all (no fix
  this boot and no usable last known position) a liveness report with `points: []` still goes
  every 30 s, so `device_last_seen_at` and the known route stay fresh. Before the first 200
  (after boot or a re-`CONFIG`) the route is *unknown*, and the 5 s cadence applies.
- **Priority.** live CRITICAL > live MEDIUM > live OK > the one DLQ critical, which goes only
  when no live event is held. Same tier: the one taken first. A live event whose attempt failed
  is retried no sooner than `MIN_SEND_GAP_MS` (1 s) after *its own* attempt ended; a brand-new
  event goes at once; a DLQ replay waits 1 s after any failed attempt.
- **Pre-emption, only between AT steps** (never after `AT+HTTPACTION` fired the request; a WiFi
  attempt is asked once, before it starts). A higher live event pre-empts a lower one; a
  critical is never pre-empted, not even by another critical; any live event pre-empts a
  points-only report or a DLQ replay. A pre-empted attempt is **not counted**: a pre-empted
  points-only report puts its points back into the track buffer; a pre-empted DLQ replay goes
  back to the Pi with `FAIL <id> preempted -` (the Pi leaves its attempts unchanged). A DLQ row
  held but not yet in flight when a live event arrives is released the same way at once.
- **Attempt deadlines** (a real deadline for the whole attempt, not an idle timeout; every AT
  step's wait is capped by what is left): WiFi 8 s; mobile 8 s on an event's first try and for a
  points-only report, 15 s on a live retry and for every DLQ replay; plus `MODEM_SETUP_TIMEOUT_MS`
  (10 s) when the modem must first be set up (after power-on or a lost signal). The deadline
  starts before the mobile registration check (`AT+CREG?`, cached for `MODEM_REG_CACHE_MS`), so
  that counts against it too. A request that timed out after `AT+HTTPACTION` fired keeps the
  SIM800L's HTTP session until its late `+HTTPACTION:` arrives: the next attempt waits for it in
  ~0.5 s chunks (still pre-emptible), and with no time left fails as `modem_busy`; past
  `GPRS_LATE_ACTION_MAX_MS` (130 s) the session is freed anyway.
- **Ages at send time.** `SenderPolicy` picks the event and the points when the attempt is
  planned, but the JSON (every `age_s`, a replay's `queued_seconds`) is composed right before
  `AT+HTTPDATA` on GPRS and right before `http.POST` on WiFi, so registration, bearer setup and
  the other AT steps are counted.
- **Live retry windows**, from when this device took the event from the Pi:

  | Event | Retried for | Then |
  |---|---|---|
  | CRITICAL | `LIVE_RETRY_CRITICAL_MS` 60 s | to the Pi's DLQ: `FAIL <id> <last error> <event JSON>` (attempt 1 there; `retry_window` if it never got an attempt) |
  | MEDIUM | `LIVE_RETRY_MEDIUM_MS` 30 s | dropped: `FAIL <id> retry_window -` (the Pi archives it) |
  | OK | `LIVE_RETRY_OK_MS` 30 s | dropped: `FAIL <id> retry_window -` |

- **The GPS points of a failed report are dropped** (user decision: the path has gaps after
  failures). Only a *pre-empted* report gives them back.
- **DLQ: one try per replay.** Delivered → `ACK`; rejected → `FAIL <id> <reason> -`; failed →
  `FAIL <id> <reason> -`, which the Pi counts. **Every attempt counts**, including ones made with
  no network; the live failure that dead-lettered the row is attempt 1, and after **5 failed
  attempts** (`BUFFER_DLQ_MAX_ATTEMPTS` on the Pi; `DLQ_MAX_ATTEMPTS` here is only for the log)
  the Pi moves the row to its `archive` table, where it is never deleted and never resent. The Pi
  serves the DLQ newest first, round-robin, so one row can't use up its attempts back to back.
  With the SIM800L this archives a critical after roughly 80 s offline.
- **Outcomes for the Pi** (`ACK`/`FAIL` of finished events) wait in their slots and ride the
  next session's report lines; they are dropped only once the Pi's `OK` line arrives, and resent
  otherwise (safe: ACK is idempotent, and the Pi ignores a repeated FAIL).

**What the serial log shows** (USB Serial, 115200; one line per operation):

```
[pi] critical (was none) "eyes_closed" class=Drowsy p=[0.05,0.95] episode=a1b2c3d4e5f6 age=0.4s => alert critical -> TAKEN
[bt] PULL: 3 record(s) -> 2 ACK, 1 TAKEN, 0 FAIL, left 0 (0 sender full); 0 outcome(s) | END dlq=0 outbox=0 | OK acked=2 failed=0 leased=1 dlq=0 outbox=0
[live] event: {"points":[...],"event":{"client_event_id":"3f2a9c...","kind":"alert","severity":"critical",...}}
[report] -> POST /api/devices/me/report via gprs (812 B, 4.2 s of 18.0 s left)
[report] <- 200 {"points":{"stored":5,...},"event":{"status":"created",...},"active_route":"6650..."}
[live] critical 3f2a9c1d attempt 1 via gprs 5.1 s: delivered
[live] points x5 attempt 1 via wifi 0.6 s: ok
[dlq] critical 7b01e2aa attempt 3/5 via gprs 15.0 s: failed (timeout)
[health] heap free 61234 B, min ever 40112 B, largest block 38900 B | stack left: sender 5120 B, loop 3010 B, gps 2100 B | held: 1 live, 0 dlq, 2 outcome(s) for the Pi (0 lost, 0 bodies cut) | route active
```

- `[live]` / `[dlq]`: one line per attempt with what it carried (`critical`/`medium`/`ok`,
  `points xN`, or `liveness`), the record id, the attempt number (`n/5` for a DLQ replay, counting
  the Pi's attempts), the transport, the duration and the result (`delivered`, `rejected`,
  `failed`, `preempted`, `ok`) with its reason. An event's composed body is printed once, as
  `[live] event:` / `[dlq] event:`, right before it goes.
- `[health]` every `TASK_HEALTH_LOG_INTERVAL_MS` (30 s): free heap, minimum ever, largest block,
  each task's stack high-water mark, what the sender holds, the known route.
- `[pi]` (and `[dlq]` for a served dead letter): one line per record, keepalives included (they
  show the camera's state every 2 s). `[bt]`: one line per session that did something, plus a
  link summary every 30 s. `[gps]`: the NEO-6M's state every 10 s. `[net]`: why there's no
  network, at most every 30 s. `[gprs]` / `[modem]`: the mobile-data steps and the modem's
  state. `[creds]`: where the credentials came from (never the key itself).

## 1. Bluetooth SPP client: pulling from `cv-argus` (protocol v2, contracts C1)

`cv-argus`'s `sender/` module runs a Bluetooth SPP **server** on the Pi (through the host's
BlueZ, see `src/cv-argus/README.md`). This device is the **client**: it opens a fresh RFCOMM
connection per session and starts every exchange; the Pi never pushes. Newline-terminated UTF-8
lines, case-sensitive. v2 is not compatible with v1 and there is no version negotiation.

```
# Live pull (every 2 s)
you -> Pi:   PULL 8                         (the Pi allows up to 50)
Pi  -> you:  <record JSON line> x0..8       outbox rows not leased, oldest first
Pi  -> you:  END <dlq_waiting> <outbox_waiting>     (always sent, even with 0 rows)
you -> Pi:   FAIL <id> <reason> <json|->    (no reply)
you -> Pi:   ACK <id1>,<id2>,...            -> Pi: ACKED <n>
you -> Pi:   TAKEN <id1>,<id2>,...          -> Pi: LEASED <n>   (the Pi leases them for 120 s)
you -> Pi:   DONE                           -> Pi: OK acked=<a> failed=<f> leased=<l> dlq=<d> outbox=<o>

# Dead-letter pull (only when END's dlq_waiting > 0, no live event is pending, and there are credentials)
you -> Pi:   DLQ 1                          at most ONE row per session
Pi  -> you:  <record JSON line with "dlq": {error, body, failed_at_ms, attempts, queued_s}> x0..1
Pi  -> you:  END <dlq_waiting> <outbox_waiting>
...report lines and DONE / OK as above
```

The grammar also has a **report session** (first line `ACK` or `FAIL`, then `DONE`), for
results of rows taken in an earlier session. This firmware doesn't need to open one: it pulls
every 2 s, and each `PULL` session carries every finished event's `ACK`/`FAIL` among its report
lines. Report lines take effect the moment the Pi reads them; `OK` is only a summary, so a session
that breaks before `OK` keeps what was already applied, and the outcomes are resent next time.

**What this device does with each record** (`pull_session.cpp`):

| Record | Reply in the same session |
|---|---|
| Unreadable or unusable (a bare NaN, a kind other than `drowsiness`, missing probabilities) | `ACK` when its id is known (so it can't block the Pi's queue); left otherwise |
| Not an event (C1.5 below: keepalives, `none`↔`low`, `critical`→`medium`, class-only changes) | `ACK`, after using its severity for the points' `vigilance` |
| An event | composed now with this device's position and `TAKEN`: the sender holds it (section 0b) |
| An event with `age_s: null` (the Pi can't tell its age) | `FAIL <id> too_old -` (archived) |
| An event whose id, episode or reason is longer than C2.1 allows | `FAIL <id> invalid -` (archived) |
| An event already held (its lease lapsed and the Pi served it again) | `TAKEN` again, one copy kept (its original live window) |
| An event when every live slot is busy | left on the Pi (served again) |

**Which records are backend events (C1.5)**, the exact rule the Pi and this device share. With
`rank(none)=0, low=1, medium=2, critical=3`, `prev = previous_severity` (missing, `null`, `""`
or any unknown word counts as `none`) and only `transition: true` rows:

| Event | Condition | Backend event |
|---|---|---|
| alert medium | `severity == medium` and `rank(prev) < 2` | `kind: "alert"`, `severity: "medium"` |
| alert critical (raise or escalation) | `severity == critical` and `rank(prev) < 3` | `kind: "alert"`, `severity: "critical"` |
| driver OK | `rank(severity) <= 1` and `rank(prev) >= 2` | `kind: "ok"` |
| *no event* | everything else | ACKed |

`test/vectors/event_vectors.json` is the executable form of this table (replayed by
`test/host/event_vectors_host_test.cpp`, cv-argus's suite and `src/tests/edge_chain`).

**A dead letter** (`DLQ 1`) is replayed from the event stored with it (`dlq.body`: the event as
this device composed it when the critical first failed, with the position where it happened),
`age_s` carried forward from the record, `delivery: "dlq"`, `queued_seconds` = `dlq.queued_s`
plus the time held here. With no stored body (`esp32_unreachable`: the row's lease lapsed before
this device could report it), the event is composed from the record with `position: null`, since
this device never saw where it happened.

**The JSON record** (`cv-argus/src/alerts/serialization.py` plus serve-time fields, C1.4):

```json
{
  "id": "3f2a…",                 // uuid4 hex: the event's client_event_id
  "kind": "drowsiness",          // the only kind in v2
  "level": 2,                    // 2 = Drowsy, 1 = Not Drowsy, null = No Face
  "created_at_ms": 1792764000123, "mono_ms": 81234567, "boot_id": "…", "clock_synced": true,
  "source_id": "pi_camera",      // a camera id, NOT a route id
  "payload": {
    "class_name": "Drowsy", "probabilities": [0.05, 0.95], "transition": true,
    "severity": "critical", "previous_severity": "medium", "episode": "a1b2c3d4e5f6",
    "reason": "eyes_closed", "model": "parts-rules-v1"
  },
  "geolocation": null,           // ALWAYS null from cv-argus: the GPS is on this device
  "age_s": 1.4,                  // computed by the Pi when it serves the line; null = unknowable
  "dlq": {"error": "no_network", "body": {…}|null, "failed_at_ms": 1792764060000,
          "attempts": 2, "queued_s": 37}            // only on rows served by DLQ 1
}
```

**How it connects.** `SerialBT.connect(PI_BT_MAC_ADDRESS, PI_BT_CHANNEL)`: straight to RFCOMM
channel 4 instead of an SDP lookup on every poll (a Windows PC playing the Pi in `test/hil/`
needs `0`). The whole batch is read before any of it is handled, because `BluetoothSerial`'s
512-byte receive queue silently drops what doesn't fit while records are being handled.
`[bt] CONNECTED to the Pi` / `[bt] Can't reach the Pi` print only when that changes.

## 1b. Credentials: fetched from the Pi, not flashed in

A truck is provisioned from the dashboard: **Fleet → truck → "Generate device credentials"**
(root_admin/admin) issues a device key (`POST /api/trucks/{id}/rotate-key`, shown once) and
downloads an `argus-device.env` (`ARGUS_TRUCK_ID`, `ARGUS_DEVICE_API_KEY`, `ARGUS_BACKEND_URL`).
That file goes next to `cv-argus`'s `docker-compose.yml` on the truck's Pi. The Pi then serves
it to this device over the same Bluetooth link, as a config session:

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
- every `CONFIG_RETRY_INTERVAL_MS` (1 min) while the Pi answers `NOCONFIG`;
- every `CONFIG_REFRESH_INTERVAL_MS` (10 min) otherwise, so a changed `.env` on the Pi (new
  WiFi, new APN) arrives without a reboot.

Only a new truck id, key or backend URL makes the known active route unknown again (until the
next 200); a routine refresh, or a new WiFi/APN alone, keeps it. The last good copy is cached in
NVS (`credentials.cpp`), so a truck that boots before its Pi still reaches the backend. With no
credentials at all the pulling goes on (the vigilance stays current) but nothing reaches the
backend: live events fail `no_credentials` through their windows, and no `DLQ 1` is asked for.
The key and the WiFi/APN passwords never go to `Serial`.

**Link security**: the Pi requires a paired, encrypted RFCOMM link (`cv-argus`'s
`BLUETOOTH_SECURITY=medium`), because this reply carries the key. This side pairs automatically
(SSP "Just Works": no display or keypad). On the Pi, cv-argus's own pairing agent accepts only
the MAC in `ESP32_BT_MAC`, so no `bluetoothctl` step is needed (`src/cv-argus/README.md`,
"Provisioning a truck"). Just Works gives encryption but no MITM protection during pairing,
which is the known limit of a display-less device.

## 2. The report: `POST /api/devices/me/report` (contracts C2)

The **only** device write. `backend-argus` resolves the truck from the key alone and routes
everything itself, so this device never needs a route id (`GET /api/devices/me/active-route`
still exists but isn't used).

- **Auth**: `X-Device-Api-Key: <key>` on every request (per truck, issued from the dashboard,
  handed over by the Pi, section 1b). Device key only, no user login.
- **Transport**: GPRS first, WiFi as the fallback (section 0). The WiFi radio is **off** at boot
  and only runs while it's needed: when the Pi gives no APN (WiFi is then the only path), or from
  a mobile-data failure (including "not registered") until a mobile request works again, then
  it's turned off. Its driver holds tens of KB of heap that classic Bluetooth needs: with WiFi up
  for nothing, the Bluetooth stack ran out of heap on a reconnect and rebooted the board (assert
  `fixed_queue_new ... lock != NULL`). Bluetooth also starts classic-only (BLE memory released),
  a Pi connect is skipped below `PI_BT_MIN_FREE_HEAP`, and an unreachable Pi is retried with a
  backoff up to `PI_BT_RETRY_MAX_MS`. Over WiFi, `https://` URLs use
  `WiFiClientSecure` with Amazon Root CA 1 pinned (`root_ca.h`); `http://` (a LAN bench backend)
  uses plain `WiFiClient`. The timeouts are the per-attempt deadlines of section 0b.
- **Times are relative.** Every `age_s` is seconds before the request, measured when the body is
  composed (right before it goes); the backend turns them into absolute times with its own clock.
  This device needs no NTP-synced clock.

**Request body, field by field:**

| Field | Source |
|---|---|
| `points[]` | one per GPS read (1 s) since the previous report, oldest first, the newest 10 kept; may be `[]` |
| `points[].age_s` | seconds from that GPS read to the moment the body is composed |
| `points[].lat`, `.lon`, `.speed` | this device's GPS: the current fix (speed in km/h), or the saved last known position with `speed: 0` |
| `points[].last_known`, `.location_age_s` | `true` and the fix's age when there's no current fix; `false` and `0` for a live fix |
| `points[].vigilance` | the Pi's latest severity at that read: `none`/`low` → `low`, `medium`, `critical`; `unknown` when no Pi record arrived for `PI_STALE_MS` (10 s) or none since boot |
| `event` | at most one, from the sender's priority (section 0b); absent on a points-only or liveness report |
| `event.client_event_id` | the Pi record's `id` (the backend's dedup key; the Pi's `ACK`/`FAIL` id) |
| `event.episode` | `payload.episode`; the record id when the Pi sent none |
| `event.kind` | `alert` or `ok`, by the C1.5 rule (section 1) |
| `event.severity`, `event.alert_type` | alerts: `medium`/`critical` and `payload.reason` (its `class_name` when there's no reason); `null` for `ok` |
| `event.age_s` | the record's `age_s` (from the Pi) plus the time held here until the body is composed |
| `event.delivery`, `event.queued_seconds` | `live` and `0`; a replay: `dlq` and `dlq.queued_s` plus the time held here |
| `event.position` | `{lat, lon, last_known, location_age_s}` when the event was first composed (the current fix, else the last known position), or `null` ("position unknown"); a replay keeps the stored one |
| `event.speed` | km/h with a current fix at compose time, else `null` |
| `event.ai_metadata` | `{model: payload.model, scores: {not_drowsy, drowsy}}` from `payload.probabilities`; `scores: null` when the Pi sent none (`No Face`) |

**Response and reaction** (C2.2/C2.3; `ack_policy.cpp`). Once the key and the envelope are
valid, the backend always answers **200** with a result per part: `{points: {stored, route,
status: ok|no_active_route|route_finished}, event: {status: created|duplicate|rejected, reason,
id_alert}|null, active_route}`.

| Answer | Reaction |
|---|---|
| 200, `event.status` `created` / `duplicate` | delivered: `ACK <id>` to the Pi |
| 200, `event.status` `rejected` (`route_finished`, `no_route`, `too_old`, `wrong_truck`, `invalid`) | stop retrying: `FAIL <id> <reason> -` (the Pi archives it); any other reason is sent as `invalid` |
| 200 without a usable `event` part when one was sent | a failed attempt, reason `bad_response` |
| 200, any `points.status` | the points are done (stored or not); the known route becomes `active_route`. While it is `null`, points-only reports slow to every 30 s; events still go at once |
| 401 | a failed attempt; re-`CONFIG` from the Pi |
| 413 / 422 | a firmware bug: the points are dropped, the event is treated as `rejected invalid` |
| 429 / 5xx / timeout / transport error / `modem_busy` | a failed attempt: the points are dropped, the event follows the retry rules (live window, then the DLQ for a critical or `retry_window` for a medium/OK; a DLQ replay's one try is counted) |

The backend routes each **event by time** (the truck's route whose departure..arrival contains
`occurred_at`; a finished one answers `route_finished`, none at all `no_route`) and the
**points** to the truck's current active route. An event after a route was stopped or completed
is therefore rejected, and the Pi archives it. A driver OK only sets `driver_ok_at` on the
episode's alerts: closing an alert is a guardian's job in the dashboard, never this device's.

## 3. Geolocation: this device's job, not cv-argus's

`cv-argus` never sets `geolocation`: the Pi has no GPS in this design (the geolocation module is
wired to the ESP32). `gpsTask` reads the NEO-6M every second:

- A **current fix** is one TinyGPSPlus updated within `GPS_FIX_MAX_AGE_MS` (2.5 s); it gives a
  point with its speed.
- With **no current fix**, the point is the **last known position**, marked `last_known: true`
  with `location_age_s` and `speed: 0`. That position is saved to NVS (`"lastpos"`, with the
  fix's GPS UTC time) every `GPS_LAST_KNOWN_SAVE_MS` (60 s) while there is a fix, and at once
  when the fix is lost, so the NVS isn't written every second. Within one boot its age comes from
  `millis()`; after a reboot it comes from GPS time, so a position restored from NVS is used only
  once the GPS has UTC time again (until then no points, and events carry `position: null`).
- With neither, there is no point (the liveness report of section 0b still goes).

An event's `position` is taken the same way when it is composed in the Bluetooth session that
pulled it, so it says where the truck was when the camera saw it, not when the report finally
got through.

## 4. Hardware ownership

Per the root `CLAUDE.md` and `docs/designs/semantic-design`, this device owns:

- **GPS** (NEO-6M): sections 3 and 0b.
- **SIM800L modem**: mobile data only (`gprs_http.*`). It sends no SMS.
- **Grip sensor** (steering wheel): would feed this device's own fusion decision loop (section
  5). `grip_sensor.*` exists (a debounced digital input on `GRIP_SENSOR_PIN`), but the GPIO and
  signal convention are a **placeholder**, not a verified mapping (see `config.h`; GPIO 34-39
  are avoided because they have no internal pull-up). Not wired yet.
- **CAN bus / AEB actuator** (preventive braking) and **alarm speaker**: **no code anywhere in
  this repo** yet; out of scope so far.

The panic button and its SMS were removed (final-tweaks plan, section 1): its pins, its NVS queue
and its alerts are gone from the firmware; `setup()` powers the modem directly
(`modemBegin()`). At boot the firmware deletes the v1 NVS keys `episodes` and `panics`.

## 5. Grip sensor + drowsiness fusion: the design for when the sensor is wired

> **Not in use** (user decision, 2026-10-20): the grip sensor isn't wired, so events follow the
> Pi's own severity (section 1, C1.5). This section is the design for when the sensor is added.
> When it is, the fused decisions must map onto the same report events (`kind: alert` medium /
> critical, `kind: ok`), not onto the retired per-message endpoints.

This is the fused severity decision this device computes from two inputs it alone has both of:
the Pi's relayed drowsiness classification and its own live grip-sensor reading. Modeled on
`cv-argus`'s own debounce/escalation/recovery vocabulary, extended to a joint two-signal state. A
typed, executable, **fully implemented and unit-tested** reference lives in
`src/cv-argus/src/orchestrator/fusion_contract.py`'s `FusionOrchestrator` (a contract reference,
not part of cv-argus's running pipeline); `fusion.*` is its 1:1 C++ port, host-tested against the
shared `test/vectors/fusion_vectors.json`. Translate changes into firmware 1:1 rather than
re-deriving them from the matrix below.

**Base severity matrix**: both signals bad → `critical`, both good → `low`, either one alone bad
→ `medium`:

| | Good grip | Bad/low grip |
|---|---|---|
| **Not Drowsy** (`p(Drowsy) < 0.57`) | `low` | `medium` |
| **Drowsy** (`p(Drowsy) >= 0.57`) | `medium` | `critical` |

(`DROWSY_PROBABILITY_THRESHOLD` = 0.57; cv-argus's `RULE_P_DROWSY` sends 0.30 for LOW and
0.75/0.95 for MEDIUM/CRITICAL, so only MEDIUM+ crosses it.)

**Timing windows** (proposed defaults, `FUSION_*` in `config.h`, not tuned against a real drive):

- **Debounce** (~5 s): the combined `(drowsy, grip)` state must hold this long before it first
  raises an alert at that severity, so one noisy frame or a gear shift doesn't fire one. The same
  short window also confirms a **sudden worsening** of an open incident (an open `medium` whose
  next reading maps straight to `critical`), not the escalation window below.
- **Escalation** (~20 s): an open `medium` that hasn't returned to `low` within this window
  escalates to `critical`, a new alert event in the same episode (the backend links it to the
  medium one). Kept at 20 s deliberately: `medium` also fires for `Not Drowsy + bad grip` (an
  alert driver with a hand off the wheel), and a faster timer would routinely escalate that
  benign case. See `fusion_contract.py`'s `FusionConfig` docstring.
- **No automatic de-escalation**: only a full recovery (both signals good for the recovery
  window) ends an episode; a partial improvement only resets the recovery clock.
- **Recovery** (~10 s): both signals good for this long ends the episode with one driver-OK
  event. As with the camera-only path, it never closes the alert: a guardian does that in the
  dashboard.
- **Failed sends are retried, never dropped**: a raise stays owed until it is confirmed sent,
  and a newer raise (an escalation) replaces an older owed one (`recordAlertPosted()`).

## Why Bluetooth, not the CAN bus/UART, for the Pi link

Already a settled decision: Bluetooth SPP, with the ESP32 as the polling client. See the root
`CLAUDE.md`'s edge-side section and `cv-argus/CLAUDE.md` for the reasoning (container-wise this
means passing the host's BlueZ/D-Bus socket or `/dev/rfcommN` through, not a UART device node).
