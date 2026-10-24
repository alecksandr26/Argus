# Grip sensor, cabin alarm and critical-alert SMS — contracts

Agreed 2026-10-23. Source: the user's bench sketch (4 × RFP602 pressure sensors on the wheel, a
buzzer, a TinyGsm SMS with a Google Maps link) plus their rules:

- No pressure on the wheel for **3 s** → a **critical** alert to the backend, the buzzer, one SMS.
  Pressure back → the grip alarm stops.
- A **critical from the vision (Pi)** → the buzzer and one SMS. A driver-OK from the Pi stops it.
  **`no_face` never sounds the buzzer** (it still gets its SMS, see C5).
- **One SMS per critical alert**, to a phone set from the dashboard and carried
  UI → `argus-device.env` → Pi → `CONFIG` → ESP32, where it lives in **RAM only**.

Teams: T1 `esp32-argus`, T2 `cv-argus`, T3 `backend-argus`, T4 `ui-argus`. Branches
`feat/grip-alarm-t1-esp32`, `-t2-pi`, `-t3-backend`, `-t4-ui`, merged into
`worktree-esp32-grip-alarm`. Nothing here changes protocol v2's message set: `CONFIG` only gains
optional JSON fields, which an older ESP32 ignores and an older Pi never sends.

## C1. The phone and plate, end to end

Phone format everywhere: **E.164**, regex `^\+[1-9][0-9]{7,14}$` (e.g. `+523312345678`). An empty
value means "no phone" (no SMS).

### C1.1 Backend (T3)
- `Truck.alert_phone: Optional[str] = None`. In the create and update schemas (optional; on update
  `null` or `""` clears it) and in the truck response. Invalid format → 422. `""` is stored as
  `None`. Same RBAC as the other truck fields (whoever may edit a truck may edit this).
- No other backend change is needed: `ReportEvent.alert_type` is a free string, so the ESP32's
  `no_grip` alert (C3) is accepted as is. Add a backend test that a report with
  `alert_type: "no_grip"`, `severity: "critical"` is created and opens/joins an incident like any
  critical.

### C1.2 UI (T4)
- `Truck` type gains `alert_phone?: string | null`. The Fleet truck create/edit form gets an
  optional "SMS alert phone" field (E.164, hint `+523312345678`, same validation client-side).
- `argus-device.env` (`utils/deviceEnv.ts`) gains two lines, after the existing three:
  - `ARGUS_TRUCK_PLATE=<plate>` (always; the plate is already in the input).
  - `ARGUS_ALERT_PHONE=<phone>` when the truck has one, otherwise the comment
    `# ARGUS_ALERT_PHONE=  (no SMS alert phone set for this truck)`.
  The dialog says that changing the phone later means generating new credentials (or editing
  that line on the Pi and restarting the container).
- `alertTypeLabel()` (`utils/status.ts`): `no_grip: 'Hands off the wheel'`.

### C1.3 Pi (T2)
- `DeviceConfig` gains `truck_plate: str | None` (env `ARGUS_TRUCK_PLATE`, stripped) and
  `alert_phone: str | None` (env `ARGUS_ALERT_PHONE`, stripped). Both optional and outside the
  "all three or none" credential check.
- An `ARGUS_ALERT_PHONE` that fails the regex is **logged as a warning and left out**, never an
  exception: a typo in the phone must not take the truck's alerts offline.
- `to_wire()` adds `"truck_plate"` and `"alert_phone"` only when set. The phone is personal data:
  `repr()` shows it masked (keep the last 4 digits, e.g. `+52******5678`), and nothing logs it in
  full.
- `docker-compose.yml` already loads `argus-device.env`; update `.env.example`/README so the two
  variables are documented.

### C1.4 ESP32 (T1)
- `DeviceCredentials` gains `String truckPlate` and `String alertPhone` (`""` = not provided).
  `parseConfigLine()` reads `"truck_plate"` (max 16 chars, else ignored) and `"alert_phone"`
  (must match the E.164 regex, else ignored with `alertPhone = ""`). **Neither field can make the
  line Malformed**: the credentials still apply.
- **RAM only**: `credentials.*` must not write `alertPhone`/`truckPlate` to NVS. After a reboot they
  are empty until the next `CONFIG` succeeds (the firmware already re-fetches `CONFIG` on every
  boot). `sameBackendIdentity()` ignores both.

## C2. Grip sensor (T1)

- Four RFP602 on **GPIO 34, 35, 36, 39** (ADC1: they keep working with Bluetooth/WiFi on),
  `analogReadResolution(12)`. **Pressure = any sensor reads `> GRIP_PRESSURE_THRESHOLD` (500)**.
  Sampled every `GRIP_SAMPLE_INTERVAL_MS` (100). Raw readings go to Serial at most every
  `GRIP_LOG_INTERVAL_MS` (2000), not every sample.
- **No pressure continuously for `GRIP_ALERT_MS` (3000)** → a grip episode starts: the grip alarm
  turns on, one live critical event is queued (C3), one SMS is queued (C5).
- **Pressure back for `GRIP_RECOVERY_MS` (300)** → the grip alarm turns off at once, the episode
  ends, and one live OK event with the same episode is queued (C3). The next 3 s without pressure
  starts a new episode.
- `GRIP_REQUIRE_MOVING` (default `0`, the user's rule as given): when `1`, the 3 s timer only runs
  while the GPS speed is at least `GRIP_MIN_SPEED_KMH` (5), so a parked truck doesn't alarm.
- The old digital `GRIP_SENSOR_PIN 19` goes. `fusion.*` stays as it is and unused (the user's rule
  makes grip its own critical source; fusion's documented state machine is for a later decision).
- Pure logic (`grip_policy.*`, injected clock, no Arduino) with a host test.

## C3. Grip events to the backend (T1)

ESP32-originated, so they are **not** Pi records: no `TAKEN`/`ACK`/`FAIL` to the Pi for them.
- Alert: `kind "alert"`, `severity "critical"`, `alert_type "no_grip"`, `delivery "live"`,
  `episode "grip-<boot>-<n>"`, `client_event_id "grip-<boot>-<n>-a"`. OK: `kind "ok"`, same
  episode, `client_event_id "grip-<boot>-<n>-ok"`. `<boot>` = 8 hex digits from `esp_random()` at
  boot (so ids don't repeat across reboots), `<n>` = episode counter from 1.
- `age_s` grows from the moment the event was raised; `position`/`speed` = the GPS at that moment
  (the last known position if there's no fix, none if neither), as the sender already does for Pi
  events.
- Sender priority: the grip alert is a **live CRITICAL** (same tier as a Pi critical; same tier →
  oldest first), the OK is a **live OK**. Retry windows as for Pi events (60 s / 30 s). When the
  window runs out the event is **dropped with a log line** (there is no Pi DLQ for it; known gap,
  documented in the README).
- Fixed-capacity local slots (no heap growth): room for one alert and one OK. If a new episode's
  alert needs the alert slot while an older unsent one holds it, the older one is dropped (logged).

## C4. Buzzer (T1)

- Buzzer on **GPIO 13**, `BUZZER_ACTIVE_HIGH 1`, continuous tone. **On while `gripAlarm ||
  visionAlarm`**, off otherwise.
- `visionAlarm` turns **on** when this device takes a **live** Pi record classified as
  `AlertCritical` whose reason is not `no_face` (`eyes_closed`, `yawn_eyes_closed`, `head_down`,
  `head_down_eyes_closed`, or any other non-`no_face` reason). It turns **off** on a live Pi record
  classified as the driver OK, or after `VISION_ALARM_MAX_MS` (300000) as a safety cap if the Pi
  never sends one. MEDIUM never sounds. DLQ replays never sound.
- A record pulled again after its lease expired (same record id) or a second critical of the same
  episode must not restart a vision alarm the driver OK already ended.
- Pure logic (`alarm_policy.*`, injected clock) with a host test; the GPIO write is a thin wrapper.

## C5. Critical-alert SMS (T1)

- **One SMS per critical episode**: each grip episode (C2) and each Pi critical episode first taken
  live (keyed by its episode; a ring of the last 8 episodes dedupes re-pulls and the same
  episode's later criticals). Not for MEDIUM, OK, or DLQ replays. `SMS_ON_NO_FACE 1`: a `no_face`
  critical gets its SMS (it doesn't sound).
- Only when `alertPhone` is set; otherwise logged once per episode and skipped.
- Text, plain ASCII/GSM-7, at most 160 chars:
  `ARGUS ALERTA CRITICA: <motivo>. Camion <plate or truck id>. https://maps.google.com/?q=<lat>,<lon>`
  (6 decimals), or `... Ubicacion desconocida.` without a position (never wait for a fix).
  Motivos: `eyes_closed` → `ojos cerrados`, `yawn_eyes_closed` → `ojos cerrados tras bostezo`,
  `head_down` → `cabeza abajo`, `head_down_eyes_closed` → `cabeza abajo y ojos cerrados`,
  `no_face` → `conductor no visible`, `no_grip` → `manos fuera del volante`, anything else → the
  raw reason.
- Sent by the **sender task** (the only modem owner), with raw AT like the rest of the firmware:
  `AT+CMGF=1`, `AT+CSCS="GSM"`, `AT+CMGS="<phone>"`, wait for `>`, the text, `0x1A`, wait for
  `+CMGS:` then `OK` (up to 60 s). Serviced between report attempts, never in the middle of an HTTP
  request. Needs the modem registered on the GSM network, with or without an APN (WiFi-only
  installs still send SMS when a phone is set).
- Queue of 4 (dedupe by episode), `SMS_MAX_ATTEMPTS 3`, `SMS_RETRY_GAP_MS 10000`; a message waiting
  for network registration is dropped (logged) after `SMS_PENDING_MAX_MS` (300000).
- Pure logic (`sms_policy.*`: queue, dedupe, retries, text composer) with a host test; the AT
  exchange is a thin wrapper next to `gprs_http`/`modem`.

## C6. Tests and docs

Each team adds tests for what it built and runs its module's whole suite: T1 `test/host/` (g++),
T2 cv-argus pytest, T3 backend pytest, T4 vitest (+ typecheck/lint). Each team updates its module's
README/CLAUDE.md. The root `CLAUDE.md` is updated at integration, not by the teams.
Nothing here has run on hardware: the RFP602 threshold (500) and the SIM800L SMS need a bench run.
