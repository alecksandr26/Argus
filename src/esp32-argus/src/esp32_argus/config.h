#pragma once

// Central config for the Argus ESP32 ("Message Sender Orchestrator") firmware: wiring, timing
// and tuning only. Nothing deployment-specific lives here -- the backend URL, device key, WiFi
// network and mobile-data APN all come from the Pi over Bluetooth (CONFIG, credentials.h), set
// in its argus-device.env / .env, and the route comes from the backend. One firmware image fits
// every truck; the only per-site value left is the Pi's Bluetooth MAC below (this device needs
// it to reach the Pi at all).

// =====================================================
// WiFi -- the fallback transport to backend-argus (mobile data goes first). The network
// (ARGUS_WIFI_SSID / ARGUS_WIFI_PASSWORD) comes from the Pi; a phone hotspot or a 4G MiFi router
// in the truck works. 2.4 GHz only. The radio stays OFF until it's needed (no APN from the Pi, or
// mobile data failing) and goes off again once mobile data works: its driver's heap is what
// classic Bluetooth needs (wifi_client.h).
// =====================================================
#define WIFI_RETRY_INTERVAL_MS 10000UL

// =====================================================
// backend-argus HTTP -- see src/backend-argus/CLAUDE.md's "Device (ESP32) auth" section.
// Nothing per-truck lives here: the backend URL and device API key come from the Pi over
// Bluetooth (CONFIG -- credentials.h), which got them from the dashboard's argus-device.env, and
// the active route comes back in every report's answer (`active_route`). One firmware image fits
// every truck. The backend URL in argus-device.env must be reachable from THIS device (a LAN IP
// with no APN set, or the public https:// URL, never "localhost"). The AWS URL must be https://:
// CloudFront's /api/* is HTTPS-only and answers 403 to plain http://. Request timeouts are the
// per-attempt deadlines below (ATTEMPT_TIMEOUT_*), not a fixed HTTP timeout.
// =====================================================
// Points-only report cadence while the backend says this truck has no active route
// (`active_route: null` in a 200): every 30 s instead of every REPORT_INTERVAL_MS. Events still go
// at once. Before the first 200 the route is unknown, not null: the 5 s cadence applies (C2.3).
// Also the cadence of the liveness report (`points: []`) sent when there is no position at all.
#define ACTIVE_ROUTE_RETRY_MS 30000UL

// =====================================================
// Reports -- POST /api/devices/me/report, the only device write (final-tweaks contracts C2, C4).
// One report = the GPS points since the last report + at most one event (alert, escalation or
// driver OK). Sent by the sender task (sender_policy.h), which owns the modem and HTTP.
// =====================================================
// A points-only report goes this long after the previous report (of any kind) ended.
#define REPORT_INTERVAL_MS 5000UL
// A live event whose attempt failed is retried no sooner than this after that attempt ended. A
// new event goes at once.
#define MIN_SEND_GAP_MS 1000UL
// At most this many points per report (the newest kept); must match backend-argus's
// app/constants.py REPORT_MAX_POINTS.
#define REPORT_MAX_POINTS 10
// One whole attempt (connect/TLS + request + answer), a real deadline, not an idle timeout.
#define ATTEMPT_TIMEOUT_WIFI_MS 8000UL
#define ATTEMPT_TIMEOUT_MOBILE_FIRST_MS 8000UL   // an event's (or a points-only report's) 1st try
#define ATTEMPT_TIMEOUT_MOBILE_RETRY_MS 15000UL  // a live retry, and every DLQ replay
// Added to a mobile attempt that must first set the modem up (after power-on or a lost signal:
// registration check + bearer).
#define MODEM_SETUP_TIMEOUT_MS 10000UL
// The modem's registration state (AT+CREG?) is polled again only when older than this, or right
// after a failed mobile attempt.
#define MODEM_REG_CACHE_MS 10000UL
// A request whose attempt timed out after AT+HTTPACTION fired keeps the modem's HTTP session
// until its "+HTTPACTION:" result arrives (the SIM800L gives up by itself after ~120 s). Past
// this, the session is freed anyway.
#define GPRS_LATE_ACTION_MAX_MS 130000UL
// How long a live event is retried before giving up on it live: a critical then goes to the
// Pi's DLQ (FAIL <reason> <event>), a medium or an OK is dropped (FAIL retry_window -). Measured
// from when this device took it from the Pi.
#define LIVE_RETRY_CRITICAL_MS 60000UL
#define LIVE_RETRY_MEDIUM_MS 30000UL
#define LIVE_RETRY_OK_MS 30000UL
// Live events from the Pi held at once (static slots). One alert per incident keeps this far from
// full; when it is full a new event isn't TAKEN and stays on the Pi, which serves it again. The
// grip sensor's own events have two more slots of their own (one alert, one OK: sender_policy.h).
#define SENDER_LIVE_SLOTS 8
// Reports to the Pi (ACK/FAIL of finished events) waiting for the next Bluetooth session. Each
// slot carries a 640 B event body, so 16 cost ~12 KB of RAM the Bluetooth stack needs; with one
// alert per incident a handful is plenty. When full, the oldest falls off: the Pi's lease then
// expires and it serves that row again (the backend de-duplicates it).
#define SENDER_OUTCOME_SLOTS 6
// The Pi archives a dead letter after this many failed attempts (cv-argus
// BUFFER_DLQ_MAX_ATTEMPTS; the Pi does the counting, this copy is only for the "attempt n/5" log).
#define DLQ_MAX_ATTEMPTS 5

// =====================================================
// Tasks -- the Arduino loop runs the Bluetooth sessions with the Pi; gpsTask reads the NEO-6M;
// senderTask owns the modem, WiFi and every request to the backend. Stacks in bytes (ESP-IDF).
// =====================================================
#define SENDER_TASK_STACK 12288  // TLS over WiFi (mbedTLS) + the report JSON
#define GPS_TASK_STACK 4096
// Heap (free, minimum ever, largest block) and every task's stack high-water mark on Serial.
#define TASK_HEALTH_LOG_INTERVAL_MS 30000UL

// =====================================================
// GPS points
// =====================================================
// One point per GPS read (current fix, or the last known position when there's none).
#define GPS_READ_INTERVAL_MS 1000UL
// A fix counts as current only while TinyGPSPlus updated it this recently (it keeps the first fix
// "valid" forever otherwise).
#define GPS_FIX_MAX_AGE_MS 2500UL
// The last good position is saved to NVS this often while there's a fix, and at once on losing it
// (with the fix's GPS time, so its age is known after a reboot).
#define GPS_LAST_KNOWN_SAVE_MS 60000UL
// A point's vigilance is the Pi's latest severity; "unknown" when no Pi record arrived for this
// long (or none since boot).
#define PI_STALE_MS 10000UL
// How often to ask the Pi for CONFIG again after it answered NOCONFIG (not provisioned).
#define CONFIG_RETRY_INTERVAL_MS 60000UL
// How often to re-ask the Pi for CONFIG even when everything works, so a changed .env on the
// Pi (new WiFi, new APN) reaches this device without a reboot or a 401.
#define CONFIG_REFRESH_INTERVAL_MS 600000UL

// =====================================================
// Mobile data (SIM800L GPRS) -- tried first for every report POST when the Pi sends an APN
// (ARGUS_GPRS_APN, with optional ARGUS_GPRS_USER / ARGUS_GPRS_PASSWORD); no APN = WiFi only.
// WiFi is the fallback. Only a transport failure (no network, no bearer, modem error) switches
// to WiFi, for GPRS_HOLD_OFF_MS, then mobile data is tried again (see transport_policy.h).
// =====================================================
#define GPRS_HOLD_OFF_MS 300000UL // 5 minutes on WiFi after a mobile-data transport failure

// =====================================================
// Bluetooth SPP client -- pulling from cv-argus's sender/ module (the Pi runs the SPP
// *server*; this device is the client -- see src/esp32-argus/README.md section 1 and
// src/cv-argus/src/sender/protocol.py).
// =====================================================
// The Pi's Bluetooth MAC address. Read it on the Pi with `bluetoothctl show` (the
// "Controller" line); it isn't the Pi's WiFi/Ethernet MAC. For the bench test (test/hil/),
// the PC plays the Pi: use the PC's Bluetooth MAC, e.g. { 0xB0, 0xDC, 0xEF, 0x34, 0x4C, 0x83 }.
#define PI_BT_MAC_ADDRESS { 0x2C, 0xCF, 0x67, 0xCA, 0xD0, 0x6E } // invision-raspberry
// The RFCOMM channel the Pi serves on: cv-argus's BLUETOOTH_CHANNEL (default 4). Connecting
// to it directly skips a service lookup (SDP) on every poll. 0 = look the channel up instead,
// which the bench PC needs: Windows picks its own channel for an incoming COM port.
#define PI_BT_CHANNEL 4
// Every poll pulls the live outbox (`PULL`); the Pi's keepalives come every 2 s, so this keeps
// what the camera sees and what this device logs about 2 s apart.
#define PI_BT_POLL_INTERVAL_MS 2000UL
// While the Pi doesn't answer, the connect is retried at 2 s, 4 s, 8 s ... up to this, instead of
// every poll: each failed connect costs the Bluetooth stack heap.
#define PI_BT_RETRY_MAX_MS 30000UL
// A connect is only attempted with this much heap free (and a free block this large): the
// Bluetooth stack aborts the whole board (assert in fixed_queue_new) when a connect can't
// allocate. Below it the attempt is skipped and logged.
#define PI_BT_MIN_FREE_HEAP 20480UL
#define PI_BT_MIN_HEAP_BLOCK 4096UL
// At most this many records per poll (the Pi allows 50). The whole batch is held in memory before
// any of it is handled (bluetoothOpenSession), so a small batch keeps the heap safe. 8 every
// PI_BT_POLL_INTERVAL_MS is still well above the Pi's one keepalive per 2 s. Nothing in a session
// waits on the backend (the sender task does that), so the Pi's 30 s wait for the report lines
// (cv-argus SenderServer ack_timeout) is never at risk.
#define PI_BT_PULL_COUNT 8
// Bytes reserved per line read from the Pi; a record is 200-400 B today (a dead letter with its
// stored event ~900 B), more just reallocates. A line longer than PI_BT_LINE_MAX ends the session.
#define PI_BT_LINE_RESERVE 768
#define PI_BT_LINE_MAX 4096
#define PI_BT_LINE_TIMEOUT_MS 3000UL
// The Pi's dead-letter queue is replayed one row per session (`DLQ 1`, the Pi's MAX_DLQ_LIMIT),
// only while no live event is pending (sender_policy.h). DLQ_REPLAY_BATCH is gone (C4).
// The Bluetooth link's status line (up/down, sessions, records) on Serial.
#define PI_BT_LINK_LOG_INTERVAL_MS 30000UL
// How often backend_api's logNoNetwork() may print why there's no network (no APN, mobile data
// on hold, the modem's registration state), so a no_network loop explains itself without
// flooding Serial.
#define NET_STATUS_LOG_INTERVAL_MS 30000UL

// =====================================================
// Grip + drowsiness fusion (fusion.*) -- NOT USED: camera alerts follow the Pi's own severity
// (pi_event_policy.h), and the grip sensor is its own critical source (grip_policy.h, contracts
// C2). Kept for a later decision on fusing the two.
// DROWSY_PROBABILITY_THRESHOLD: cv-argus's RULE_P_DROWSY is built around it (LOW 0.30 below,
// MEDIUM/CRITICAL 0.75/0.95 at or above; tests/test_constants.py guards that side).
// =====================================================
#define DROWSY_PROBABILITY_THRESHOLD 0.57f

// Grip + drowsiness fusion timing windows -- mirrors
// src/cv-argus/src/orchestrator/fusion_contract.py's FusionConfig defaults exactly. See
// that file and src/esp32-argus/README.md section 5 before changing these; they're
// proposed defaults, not tuned against a real drive yet.
// =====================================================
#define FUSION_DEBOUNCE_SECONDS 5.0f
#define FUSION_ESCALATION_SECONDS 20.0f
#define FUSION_RECOVERY_SECONDS 10.0f

// =====================================================
// Hardware pins
// =====================================================

// SIM800L -- mobile data (gprs_http.h) and the critical-alert SMS (sms_modem.h; the old panic SMS
// went with the panic button). Pins as originally wired.
#define MODEM_RST 5
#define MODEM_PWKEY 4
#define MODEM_POWER_ON 23
#define MODEM_TX 27
#define MODEM_RX 26

// NEO-6M GPS, as originally wired.
#define GPS_RX 32
#define GPS_TX 33
#define GPS_BAUD 9600

// Bench testing only (src/esp32-argus/test/hil/). Indoors the NEO-6M usually has no fix; reports
// still go without one (an event then says "position unknown", and with no last known position
// only empty liveness reports go), so 1 just makes gps_module report a fixed position instead,
// for a bench that shows points and alert positions. GPS date/time stays real (none indoors).
// NEVER flash a truck with this set to 1: every point and alert would carry these coordinates.
#define BENCH_FAKE_GPS 0
#define BENCH_GPS_LAT 20.6597
#define BENCH_GPS_LON -103.3496
#define GPS_LOG_INTERVAL_MS 10000UL // gpsLogStatus() cadence on Serial

// =====================================================
// Steering-wheel grip sensor (docs/designs/grip-alarm-contracts.md C2) -- four RFP602 pressure
// sensors on the wheel, read as analog inputs (grip_sensor.h, grip_policy.h). ADC1 pins, which
// keep working while Bluetooth/WiFi are on (ADC2 doesn't with WiFi). Input-only pins are fine
// here: an analog read needs no pull-up. The old digital GRIP_SENSOR_PIN (19) is gone.
// =====================================================
#define GRIP_SENSOR_COUNT 4
#define GRIP_SENSOR_PINS { 34, 35, 36, 39 }
// Pressure = any sensor reads above this (12-bit ADC, 0..4095). From the user's bench sketch;
// NOT tuned against the real wheel yet.
#define GRIP_PRESSURE_THRESHOLD 500
#define GRIP_SAMPLE_INTERVAL_MS 100UL
// The four raw readings go to Serial at most this often, not every sample.
#define GRIP_LOG_INTERVAL_MS 2000UL
// No pressure continuously this long starts a grip episode: the buzzer, one live critical
// ("no_grip") for the backend and one SMS.
#define GRIP_ALERT_MS 3000UL
// Pressure back this long ends the episode: the grip alarm stops at once, one live OK goes.
#define GRIP_RECOVERY_MS 300UL
// 1 = the GRIP_ALERT_MS timer only runs while the GPS speed is at least GRIP_MIN_SPEED_KMH (with
// a current fix), so a parked truck doesn't alarm. 0 (the user's rule as given) = it always runs.
#define GRIP_REQUIRE_MOVING 0
#define GRIP_MIN_SPEED_KMH 5.0f
#define GRIP_TASK_STACK 4096

// =====================================================
// Cabin buzzer (contracts C4) -- an active buzzer: a continuous tone while its pin is on. On
// while the grip alarm or the vision alarm is (alarm_policy.h).
// =====================================================
#define BUZZER_PIN 13
#define BUZZER_ACTIVE_HIGH 1
// A vision alarm (a live critical from the camera, not no_face) stops on the Pi's driver OK, or
// after this long as a safety cap if that OK never comes.
#define VISION_ALARM_MAX_MS 300000UL

// =====================================================
// Critical-alert SMS (contracts C5) -- one SMS per critical episode (a grip episode, or a Pi
// critical first taken live) to the phone the Pi sends in CONFIG (ARGUS_ALERT_PHONE, RAM only).
// Sent by the sender task with raw AT on the SIM800L, between report attempts (sms_policy.h,
// sms_modem.h). Needs the modem registered on the GSM network; no APN is needed.
// =====================================================
// 1 = a no_face critical gets its SMS too (it never sounds the buzzer).
#define SMS_ON_NO_FACE 1
// Messages waiting at once; a new one when full drops the oldest waiting one (logged).
#define SMS_QUEUE_SLOTS 4
// The last this many Pi episodes that got (or skipped) their SMS: a re-pulled record or the same
// episode's later critical sends nothing.
#define SMS_EPISODE_MEMORY 8
#define SMS_MAX_ATTEMPTS 3
#define SMS_RETRY_GAP_MS 10000UL
// A message still unsent this long after it was queued (no GSM registration, or retries) is
// dropped (logged): never wait forever.
#define SMS_PENDING_MAX_MS 300000UL
// The wait for "+CMGS:" after the text is submitted (Ctrl-Z).
#define SMS_SEND_TIMEOUT_MS 60000UL
// "[sms] waiting for the GSM network" at most this often while a message waits.
#define SMS_WAIT_LOG_INTERVAL_MS 30000UL
