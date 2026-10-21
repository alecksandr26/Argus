// Argus ESP32 — "Message Sender Orchestrator" (see the root CLAUDE.md and this module's
// own README.md). Owns everything actuation-/safety-critical in the truck cabin and is
// the only device that talks to the cloud backend:
//
//   - Pulls queued Alert/RouteStatus records off the Pi's Bluetooth SPP server
//     (bluetooth_client.*), polling PI_BT_POLL_INTERVAL_MS -- the Pi never pushes.
//   - Gets this truck's backend credentials from the Pi too (CONFIG, credentials.*): at
//     boot and after any 401, so a key issued/rotated in the dashboard reaches this device
//     without a reflash. Cached in NVS for boots where the Pi isn't up yet.
//   - Fuses a pulled drowsiness reading with this device's own live grip-sensor reading
//     into a severity (fusion.*), and relays the result to backend-argus over mobile data
//     (SIM800L GPRS, gprs_http.*) with WiFi as the fallback (wifi_client.*, backend_api.*),
//     attaching its own GPS reading (gps_module.*) -- cv-argus never has GPS and never
//     computes a grip-aware severity itself.
//   - Relays a pulled route_status heartbeat the same way, mirroring vigilance onto the
//     fusion engine's last-confirmed severity.
//   - Handles the panic button (panic_button.*) completely outside the fusion loop:
//     unconditional `critical`, no debounce, fires both an SMS (sms_module.* via the
//     SIM800L -- a hardware-direct fallback that works even when the backend is
//     unreachable) and the normal POST /api/alerts.
//
// See config.h for every placeholder that needs a real value before this is flashed.

#include "ack_policy.h"
#include "backend_api.h"
#include "bluetooth_client.h"
#include "config.h"
#include "credentials.h"
#include "fusion.h"
#include "gps_module.h"
#include "grip_sensor.h"
#include "panic_button.h"
#include "sms_module.h"
#include "wifi_client.h"

namespace {
FusionOrchestrator fusion;
unsigned long lastBtPollAt = 0;
unsigned long lastGpsLogAt = 0;
unsigned long lastBtLogAt = 0;
String lastPiSeverity;  // BT_LINK_TEST_MODE: the banner fires on the change into critical
}  // namespace

// BT_LINK_TEST_MODE's record handler: print what the Pi sent, run the fusion and print what it
// would raise, and ACK. No GPS, network or backend involved -- see config.h.
bool handlePiRecordLinkTest(const PiRecord& record) {
  if (record.kind == PiRecordKind::ROUTE_STATUS) {
    Serial.println("[pi] route_status heartbeat");
    return true;
  }

  Serial.print("[pi] ");
  Serial.print(record.className);
  if (record.severity.length() > 0) {
    Serial.print("  severity=");
    Serial.print(record.severity);
  }
  if (record.reason.length() > 0) {
    Serial.print("  \"");
    Serial.print(record.reason);
    Serial.print("\"");
  }
  Serial.print("  p(Drowsy)=");
  Serial.println(record.pDrowsy, 2);

  if (record.severity == "critical" && lastPiSeverity != "critical") {
    Serial.println("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!");
    Serial.print("!!! CRITICAL alert received from the Pi: ");
    Serial.println(record.reason.length() > 0 ? record.reason : record.className);
    Serial.println("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!");
  } else if (lastPiSeverity == "critical" && record.severity != "critical") {
    Serial.println("[pi] critical cleared");
  }
  lastPiSeverity = record.severity;

  DrowsyState drowsyState = (record.pDrowsy >= DROWSY_PROBABILITY_THRESHOLD)
                                 ? DrowsyState::DROWSY
                                 : DrowsyState::NOT_DROWSY;
  GripState grip = readGripState();
  FusionDecision decision = fusion.update({drowsyState, grip, (float)(millis() / 1000.0)});
  if (decision.alert.present) {
    Serial.print("[fusion] would raise a ");
    Serial.print(severityToString(decision.alert.severityLevel));
    Serial.print(" alert (grip ");
    Serial.print(grip == GripState::GOOD ? "good" : "bad");
    Serial.println(decision.alert.isEscalation ? ", escalation) -- not sent, link test mode"
                                               : ") -- not sent, link test mode");
    // Keep the fusion's incident bookkeeping consistent, as if the POST had succeeded.
    fusion.recordAlertPosted(record.id);
  }
  if (decision.hasResolvedIncident) {
    Serial.println("[fusion] incident resolved -- not sent, link test mode");
  }
  return true;
}

// Called once per record bluetoothPullAlerts() pulls off the Pi. Returns true iff the
// record was successfully relayed onward -- see bluetooth_client.h's doc comment for why
// that return value is what gates the ACK.
bool handlePiRecord(const PiRecord& record) {
  if (!gpsHasFix()) {
    Serial.println("[main] No GPS fix yet -- can't attach coordinates, not relaying");
    return false;  // don't ACK -- the Pi will re-serve this once we have a fix
  }

  wifiEnsureConnected();
  if (!backendIsReachable()) {
    Serial.println("[main] No mobile data or WiFi -- not relaying");
    return false;
  }

  double lat = gpsLatitude();
  double lon = gpsLongitude();
  float speedKmh = (float)gpsSpeedKmh();

  if (record.kind == PiRecordKind::ROUTE_STATUS) {
    // odometer: no sensor on this device at all (see config.h) -- 0.0f, not a fabricated
    // running total from GPS-distance integration. The Pi record id dedups retries; the
    // GPS time (when fresh) is when this position was actually read.
    int status = backendPostRouteStatus(lat, lon, speedKmh, /*odometer=*/0.0f,
                                        fusion.confirmedSeverity(), record.id, gpsUtcIso8601());
    RelayAction action = relayActionFor(status, true);
    if (action != RelayAction::Ack) {
      Serial.print("[main] route_status ");
      Serial.print(record.id);
      Serial.print(": ");
      Serial.println(relayActionName(action));
    }
    return shouldAck(action);
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

// Asks the Pi for CONFIG when the cached credentials are stale (every boot, after a 401, and
// periodically after a NOCONFIG). A failed fetch keeps whatever NVS copy exists.
void refreshCredentialsIfStale() {
  if (!credentialsStale()) return;
  DeviceCredentials fresh;
  switch (bluetoothFetchConfig(fresh)) {
    case ConfigLineResult::Ok:
      credentialsApply(fresh);
      break;
    case ConfigLineResult::NoConfig:
      credentialsNoneOnPi();
      break;
    case ConfigLineResult::Malformed:
      break;  // still stale: retried on the next poll
  }
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
  if (hasFix && backendIsReachable()) {
    backendPostAlert(Severity::Critical, "panic_button", /*isFusionSource=*/false,
                      /*pNotDrowsy=*/0.0f, /*pDrowsy=*/0.0f, /*gripStatus=*/GripState::GOOD,
                      /*isEscalation=*/false, /*relatedAlertId=*/String(), lat, lon,
                      (float)gpsSpeedKmh());
  } else {
    Serial.println("[main] Panic SMS sent, but couldn't also POST /api/alerts "
                    "(no GPS fix and/or no mobile data or WiFi)");
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println();
  Serial.println("==== Argus ESP32 -- Message Sender Orchestrator ====");
  if (BT_LINK_TEST_MODE) {
    Serial.println("[main] BT_LINK_TEST_MODE is on -- printing what the Pi sends, nothing goes "
                   "to the backend");
  }
  if (BENCH_FAKE_GPS) {
    Serial.println("[main] WARNING: BENCH_FAKE_GPS is on -- reporting a fixed bench position");
  }

  gpsBegin();
  panicButtonBegin();
  gripSensorBegin();
  smsBegin();
  credentialsBegin();  // before wifiBegin(): the WiFi to join is in the NVS cache
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
  if (now - lastGpsLogAt >= GPS_LOG_INTERVAL_MS) {
    lastGpsLogAt = now;
    gpsLogStatus();
  }

  if (BT_LINK_TEST_MODE && now - lastBtLogAt >= BT_LINK_LOG_INTERVAL_MS) {
    lastBtLogAt = now;
    bluetoothLogStatus();
  }

  unsigned long pollInterval = BT_LINK_TEST_MODE ? BT_LINK_TEST_POLL_INTERVAL_MS
                                                 : PI_BT_POLL_INTERVAL_MS;
  if (now - lastBtPollAt >= pollInterval) {
    lastBtPollAt = now;
    if (BT_LINK_TEST_MODE) {
      bluetoothPullAlerts(handlePiRecordLinkTest);
      return;
    }
    refreshCredentialsIfStale();
    if (!credentialsReady()) {
      // Nothing could be relayed, so don't pull: records stay safely queued on the Pi.
      return;
    }
    int pulled = bluetoothPullAlerts(handlePiRecord);
    if (pulled > 0) {
      Serial.print("[main] Pulled ");
      Serial.print(pulled);
      Serial.println(" record(s) from the Pi");
    }
  }
}
