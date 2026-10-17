// Argus ESP32 — "Message Sender Orchestrator" (see the root CLAUDE.md and this module's
// own README.md). Owns everything actuation-/safety-critical in the truck cabin and is
// the only device that talks to the cloud backend:
//
//   - Pulls queued Alert/RouteStatus records off the Pi's Bluetooth SPP server
//     (bluetooth_client.*), polling PI_BT_POLL_INTERVAL_MS -- the Pi never pushes.
//   - Fuses a pulled drowsiness reading with this device's own live grip-sensor reading
//     into a severity (fusion.*), and relays the result to backend-argus over WiFi
//     (wifi_client.*, backend_api.*), attaching its own GPS reading (gps_module.*) --
//     cv-argus never has GPS and never computes a grip-aware severity itself.
//   - Relays a pulled route_status heartbeat the same way, mirroring vigilance onto the
//     fusion engine's last-confirmed severity.
//   - Handles the panic button (panic_button.*) completely outside the fusion loop:
//     unconditional `critical`, no debounce, fires both an SMS (sms_module.* via the
//     SIM800L -- a hardware-direct fallback that works even without WiFi) and the normal
//     POST /api/alerts.
//
// See config.h for every placeholder that needs a real value before this is flashed.

#include "backend_api.h"
#include "bluetooth_client.h"
#include "config.h"
#include "fusion.h"
#include "gps_module.h"
#include "grip_sensor.h"
#include "panic_button.h"
#include "sms_module.h"
#include "wifi_client.h"

namespace {
FusionOrchestrator fusion;
unsigned long lastBtPollAt = 0;
}  // namespace

// Called once per record bluetoothPullAlerts() pulls off the Pi. Returns true iff the
// record was successfully relayed onward -- see bluetooth_client.h's doc comment for why
// that return value is what gates the ACK.
bool handlePiRecord(const PiRecord& record) {
  if (!gpsHasFix()) {
    Serial.println("[main] No GPS fix yet -- can't attach coordinates, not relaying");
    return false;  // don't ACK -- the Pi will re-serve this once we have a fix
  }

  wifiEnsureConnected();
  if (!wifiIsConnected()) {
    Serial.println("[main] WiFi down -- not relaying");
    return false;
  }

  double lat = gpsLatitude();
  double lon = gpsLongitude();
  float speedKmh = (float)gpsSpeedKmh();

  if (record.kind == PiRecordKind::ROUTE_STATUS) {
    // odometer: no sensor on this device at all (see config.h) -- 0.0f, not a fabricated
    // running total from GPS-distance integration.
    return backendPostRouteStatus(lat, lon, speedKmh, /*odometer=*/0.0f,
                                   fusion.confirmedSeverity());
  }

  // DROWSINESS: feed the fusion engine -- never a 1:1 relay, see
  // src/esp32-argus/README.md section 5 and fusion.h.
  DrowsyState drowsyState = (record.pDrowsy >= DROWSY_PROBABILITY_THRESHOLD)
                                 ? DrowsyState::DROWSY
                                 : DrowsyState::NOT_DROWSY;
  FusionReading reading{drowsyState, readGripState(), (float)(millis() / 1000.0)};
  FusionDecision decision = fusion.update(reading);

  bool ok = true;  // "nothing to post this tick" still counts as successfully processed

  if (decision.alert.present) {
    AlertPostResult result = backendPostAlert(
        decision.alert.severityLevel, record.className, /*isFusionSource=*/true,
        record.pNotDrowsy, record.pDrowsy, decision.alert.gripStatus,
        decision.alert.isEscalation, decision.alert.relatedAlertId, lat, lon, speedKmh);
    if (result.ok) fusion.recordAlertPosted(result.idAlert);
    ok = result.ok;
  }

  if (decision.hasResolvedIncident) {
    String resolvedAt = gpsUtcIso8601();
    if (resolvedAt.length() == 0) {
      Serial.println("[main] Can't resolve incident yet -- no GPS time");
      ok = false;  // retry on the next pull once GPS has a date/time fix
    } else {
      ok = backendResolveAlert(decision.resolvedIncidentId, resolvedAt) && ok;
    }
  }

  return ok;
}

void handlePanicButton() {
  Serial.println("[main] PANIC BUTTON pressed");

  bool hasFix = gpsHasFix();
  double lat = hasFix ? gpsLatitude() : 0.0;
  double lon = hasFix ? gpsLongitude() : 0.0;

  // Hardware-direct fallback -- fires even if WiFi/backend-argus is unreachable. Never
  // gated on hasFix/wifi below; this is the point of keeping SIM800L/SMS at all.
  smsSendPanicAlert(hasFix, lat, lon);

  // Normal path too, per README section 5: unconditional critical, no debounce, no
  // interaction with any open fusion incident.
  wifiEnsureConnected();
  if (hasFix && wifiIsConnected()) {
    backendPostAlert(Severity::Critical, "panic_button", /*isFusionSource=*/false,
                      /*pNotDrowsy=*/0.0f, /*pDrowsy=*/0.0f, /*gripStatus=*/GripState::GOOD,
                      /*isEscalation=*/false, /*relatedAlertId=*/String(), lat, lon,
                      (float)gpsSpeedKmh());
  } else {
    Serial.println("[main] Panic SMS sent, but couldn't also POST /api/alerts "
                    "(no GPS fix and/or WiFi down)");
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println();
  Serial.println("==== Argus ESP32 -- Message Sender Orchestrator ====");
  if (BENCH_FAKE_GPS) {
    Serial.println("[main] WARNING: BENCH_FAKE_GPS is on -- reporting a fixed bench position");
  }

  gpsBegin();
  panicButtonBegin();
  gripSensorBegin();
  smsBegin();
  wifiBegin();
  bluetoothClientBegin();
}

void loop() {
  gpsPoll();

  if (panicButtonPressed()) {
    handlePanicButton();
  }

  wifiEnsureConnected();

  unsigned long now = millis();
  if (now - lastBtPollAt >= PI_BT_POLL_INTERVAL_MS) {
    lastBtPollAt = now;
    int pulled = bluetoothPullAlerts(handlePiRecord);
    if (pulled > 0) {
      Serial.print("[main] Pulled ");
      Serial.print(pulled);
      Serial.println(" record(s) from the Pi");
    }
  }
}
