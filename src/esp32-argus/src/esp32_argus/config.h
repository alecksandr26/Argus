#pragma once

// Central config for the Argus ESP32 ("Message Sender Orchestrator") firmware. Every
// `CHANGE_ME` placeholder below is a real per-device/per-deployment value this firmware
// cannot know on its own -- fill them in before flashing, don't leave the placeholder in.

// =====================================================
// WiFi -- the fallback transport to backend-argus (mobile data over the SIM800L is tried
// first, see the GPRS section below), and the only path for PUT requests. Point it at a phone
// hotspot or a 4G MiFi router in the truck to get mobile data over WiFi.
// =====================================================
#define WIFI_SSID "Casa " // trailing space is part of the real SSID (2.4 GHz; "Casa 5g" is 5 GHz-only)
#define WIFI_PASSWORD "1212121212"
#define WIFI_CONNECT_TIMEOUT_MS 20000UL
#define WIFI_RETRY_INTERVAL_MS 10000UL

// =====================================================
// backend-argus HTTP -- see src/backend-argus/CLAUDE.md's "Device (ESP32) auth" section.
// Nothing per-truck lives here anymore: the backend URL and device API key come from the Pi
// over Bluetooth (CONFIG -- credentials.h), which got them from the dashboard's
// argus-device.env; the route id comes from GET /api/devices/me/active-route. One firmware
// image fits every truck. The backend URL in argus-device.env must be reachable from THIS
// device (a LAN IP with USE_GPRS 0, or the public https:// URL, never "localhost"). The AWS
// URL must be https://: CloudFront's /api/* is HTTPS-only and answers 403 to plain http://.
// =====================================================
// Generous on purpose: a Lambda cold start behind CloudFront can take several seconds.
#define BACKEND_HTTP_TIMEOUT_MS 15000
// How often to retry GET /api/devices/me/active-route while the truck has no active route.
#define ACTIVE_ROUTE_RETRY_MS 30000UL
// How often to ask the Pi for CONFIG again after it answered NOCONFIG (not provisioned).
#define CONFIG_RETRY_INTERVAL_MS 60000UL

// =====================================================
// Mobile data (SIM800L GPRS) -- tried first for every GET/POST; WiFi is the fallback. Only a
// transport failure (no network, no bearer, modem error) switches to WiFi, for
// GPRS_HOLD_OFF_MS, then mobile data is tried again (see transport_policy.h). Set USE_GPRS 0
// to go WiFi-only (bench, or a truck with a 4G hotspot and no data plan on the SIM).
// =====================================================
#define USE_GPRS 1
// Telcel's public APN. Other carriers: AT&T Mexico "internet.attmex.mx" (no user/password),
// Movistar Mexico "internet.movistar.mx" (user/password "movistar").
#define GPRS_APN "internet.itelcel.com"
#define GPRS_USER "webgprs"
#define GPRS_PASSWORD "webgprs2002"
#define GPRS_HOLD_OFF_MS 300000UL // 5 minutes on WiFi after a mobile-data transport failure

// =====================================================
// Bluetooth SPP client -- pulling from cv-argus's sender/ module (the Pi runs the SPP
// *server*; this device is the client -- see src/esp32-argus/README.md section 1 and
// src/cv-argus/src/sender/protocol.py).
// =====================================================
// The Pi's Bluetooth MAC address, e.g. {0x08, 0x3A, 0xF2, 0x4B, 0x9C, 0x01}. Find it on
// the Pi with `hciconfig` or `bluetoothctl` ("show" on the default controller).
#define PI_BT_MAC_ADDRESS { 0xB0, 0xDC, 0xEF, 0x34, 0x4C, 0x83 } // bench: this PC's Bluetooth
#define PI_BT_POLL_INTERVAL_MS 5000UL
#define PI_BT_PULL_COUNT 50 // matches sender/protocol.py's DEFAULT_PULL_LIMIT
#define PI_BT_LINE_TIMEOUT_MS 3000UL

// =====================================================
// Drowsiness decision threshold -- MUST match cv-argus's own FUSED_MODEL_THRESHOLD
// (src/cv-argus/src/constants.py). Hand-synced, no automated check -- the same
// verbatim-duplication pattern this project already uses for Severity (see root CLAUDE.md
// and fusion_contract.py's own Severity docstring).
// =====================================================
#define DROWSY_PROBABILITY_THRESHOLD 0.57f

// =====================================================
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
// handlePiRecord() relays nothing without one, so 1 makes gps_module report a fixed position
// instead. GPS date/time stays real (empty with no fix), so an incident can't be resolved on
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
