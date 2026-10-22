#pragma once

// Central config for the Argus ESP32 ("Message Sender Orchestrator") firmware: wiring, timing
// and tuning only. Nothing deployment-specific lives here -- the backend URL, device key, WiFi
// network and mobile-data APN all come from the Pi over Bluetooth (CONFIG, credentials.h), set
// in its argus-device.env / .env, and the route comes from the backend. One firmware image fits
// every truck; the only per-site value left is the Pi's Bluetooth MAC below (this device needs
// it to reach the Pi at all).

// =====================================================
// WiFi -- the fallback transport to backend-argus, and the only path for PUT requests. The
// network (ARGUS_WIFI_SSID / ARGUS_WIFI_PASSWORD) comes from the Pi; a phone hotspot or a 4G
// MiFi router in the truck works. 2.4 GHz only.
// =====================================================
#define WIFI_CONNECT_TIMEOUT_MS 20000UL
#define WIFI_RETRY_INTERVAL_MS 10000UL

// =====================================================
// backend-argus HTTP -- see src/backend-argus/CLAUDE.md's "Device (ESP32) auth" section.
// Nothing per-truck lives here anymore: the backend URL and device API key come from the Pi
// over Bluetooth (CONFIG -- credentials.h), which got them from the dashboard's
// argus-device.env; the route id comes from GET /api/devices/me/active-route. One firmware
// image fits every truck. The backend URL in argus-device.env must be reachable from THIS
// device (a LAN IP with no APN set, or the public https:// URL, never "localhost"). The AWS
// URL must be https://: CloudFront's /api/* is HTTPS-only and answers 403 to plain http://.
// =====================================================
// Generous on purpose: a Lambda cold start behind CloudFront can take several seconds.
#define BACKEND_HTTP_TIMEOUT_MS 15000
// How often to retry GET /api/devices/me/active-route while the truck has no active route.
#define ACTIVE_ROUTE_RETRY_MS 30000UL
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
// any of it is handled (see bluetoothExchange), and each record is logged and possibly sent before
// the Pi gets the report, so a small batch keeps both the heap and the Pi's 30 s wait safe. 8
// every PI_BT_POLL_INTERVAL_MS is still well above the Pi's one keepalive per 2 s.
#define PI_BT_PULL_COUNT 8
// Bytes reserved per line read from the Pi; a record is 200-400 B today, more just reallocates.
#define PI_BT_LINE_RESERVE 768
#define PI_BT_LINE_TIMEOUT_MS 3000UL
// Once the backend is reachable, the Pi's dead-letter queue is replayed this many records per
// poll (`DLQ <k>`), after the live ones: the newest events always go first.
#define DLQ_REPLAY_BATCH 5
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

// SIM800L -- panic SMS (sms_module.h) and mobile data (gprs_http.h). Pins as originally wired.
#define MODEM_RST 5
#define MODEM_PWKEY 4
#define MODEM_POWER_ON 23
#define MODEM_TX 27
#define MODEM_RX 26
#define SMS_NETWORK_WAIT_MS 240000UL // 4 minutes, matches the original prototype

// NEO-6M GPS, as originally wired.
#define GPS_RX 32
#define GPS_TX 33
#define GPS_BAUD 9600

// Bench testing only (src/esp32-argus/test/hil/). Indoors the NEO-6M usually has no fix, and
// a message without a position can't be sent (it goes to the Pi's dead-letter queue as
// no_gps), so 1 makes gps_module report a fixed position instead. GPS date/time stays real (empty with no fix), so an incident can't be resolved on
// the bench. NEVER flash a truck with this set to 1: every alert would carry these coordinates.
#define BENCH_FAKE_GPS 0
#define BENCH_GPS_LAT 20.6597
#define BENCH_GPS_LON -103.3496
#define GPS_LOG_INTERVAL_MS 10000UL // gpsLogStatus() cadence on Serial

// Panic button + grip sensor -- GPIO mapping not finalized against real hardware yet
// (src/esp32-argus/README.md section 4 says so explicitly: "none of them have a defined
// GPIO pin mapping ... yet"). Wired here as active-low digital inputs with an internal
// pull-up so both modules are runnable/testable today with just a button/switch to GND --
// move these pins if the real hardware harness ends up wired differently.
//
// Deliberately NOT using GPIO 34-39: those ESP32 pins are input-only and have no internal
// pull-up/pull-down transistor at all, so `pinMode(..., INPUT_PULLUP)` silently does
// nothing on them -- a real hardware gotcha, not a style choice.
#define PANIC_BUTTON_PIN 18
#define PANIC_BUTTON_DEBOUNCE_MS 50UL
#define GRIP_SENSOR_PIN 19

// Panic SMS destination -- carried over from the original prototype.
#define SMS_DESTINATION_NUMBER "+523314033063"
