#pragma once

// Central config for the Argus ESP32 ("Message Sender Orchestrator") firmware. Every
// `CHANGE_ME` placeholder below is a real per-device/per-deployment value this firmware
// cannot know on its own -- fill them in before flashing, don't leave the placeholder in.

// =====================================================
// WiFi -- the main transport to backend-argus and (indirectly, see bluetooth_client.h)
// the Bluetooth pull from cv-argus. Per-project decision: this device's WiFi radio joins
// a mobile-network-backed WiFi link, NOT the SIM800L's own GPRS -- the SIM800L is kept
// SMS-only now (see sms_module.h).
// =====================================================
#define WIFI_SSID "Casa " // trailing space is part of the real SSID (2.4 GHz; "Casa 5g" is 5 GHz-only)
#define WIFI_PASSWORD "1212121212"
#define WIFI_CONNECT_TIMEOUT_MS 20000UL
#define WIFI_RETRY_INTERVAL_MS 10000UL

// =====================================================
// backend-argus HTTP -- see src/backend-argus/CLAUDE.md's "Device (ESP32) auth" section.
// BACKEND_BASE_URL must be reachable from THIS device's WiFi network -- e.g. the LAN IP of
// whatever machine is running `docker compose up` on port 8000, not "localhost" (which
// from this device's own network stack means itself, not the dev machine).
// =====================================================
#define BACKEND_BASE_URL "http://192.168.100.67:8000"

// Per-truck device API key -- minted once via POST /api/trucks/{id}/rotate-key
// (root_admin only, see backend CLAUDE.md), provisioned here out of band. Never commit a
// real deployment key to source control -- this placeholder is intentionally a dummy.
#define DEVICE_API_KEY "argus-bench-device-key"

// UNDEFINED per src/esp32-argus/README.md section 2's field-mapping table: nothing today
// tells this device which Route document a given truck's alert/status belongs to.
// Hardcoding one route id per truck here is a stopgap, not the real answer (the README's
// own suggestion -- this device querying "the truck's current in-progress route" -- isn't
// built anywhere yet). Flagged, not solved.
#define ROUTE_ID "64b0a0000000000000000003"

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

// SIM800L -- SMS only now (see sms_module.h). Pins as originally wired.
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
