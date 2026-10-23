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
// in the truck works. 2.4 GHz only.
// =====================================================
#define WIFI_CONNECT_TIMEOUT_MS 20000UL
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
// Live events held at once (static slots). One alert per incident keeps this far from full; when
// it is full a new event isn't TAKEN and stays on the Pi, which serves it again.
#define SENDER_LIVE_SLOTS 8
// Reports to the Pi (ACK/FAIL of finished events) waiting for the next Bluetooth session.
#define SENDER_OUTCOME_SLOTS 16
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
// Mobile data (SIM800L GPRS) -- tried first for every GET/POST when the Pi sends an APN
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
// How often backendIsReachable() may print why there's no network (no APN, mobile data on hold,
// the modem's registration state), so a no_network loop explains itself without flooding Serial.
#define NET_STATUS_LOG_INTERVAL_MS 30000UL

// =====================================================
// Grip + drowsiness fusion (fusion.*) -- NOT USED while there's no grip sensor: alerts follow the
// Pi's own severity (pi_event_policy.h). Kept for when the sensor is wired.
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

// SIM800L -- mobile data only (gprs_http.h); the panic SMS is gone with the panic button. Pins as
// originally wired.
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

// Grip sensor -- GPIO mapping not finalized against real hardware yet (src/esp32-argus/README.md
// section 4). Wired as an active-low digital input with an internal pull-up. Not GPIO 34-39: those
// pins are input-only and have no internal pull-up, so INPUT_PULLUP silently does nothing on them.
// (The panic button and its SMS were removed: final-tweaks plan, section 1.)
#define GRIP_SENSOR_PIN 19
