// Argus ESP32 — "Message Sender Orchestrator" (see the root CLAUDE.md and this module's
// own README.md). Owns everything actuation-/safety-critical in the truck cabin and is
// the only device that talks to the cloud backend:
//
//   - Pulls what the Pi's camera pipeline decided (cv-argus's sender/) over Bluetooth every
//     PI_BT_POLL_INTERVAL_MS -- the Pi never pushes -- and logs every record: severity,
//     reason, probabilities, and this device's GPS position.
//   - Gets this truck's backend credentials from the Pi too (CONFIG, credentials.*): at boot,
//     after any 401, and every 10 minutes. Cached in NVS for boots where the Pi isn't up yet.
//     Missing credentials never stop the pulling: every record is still logged and its backend
//     message composed.
//   - Turns the Pi's severity into backend calls (pi_event_policy.*): a rise to medium/critical
//     is an alert, a fall back resolves it, a heartbeat posts the truck's position. There is no
//     grip sensor yet, so fusion.* isn't used. Each message is composed (backend_messages.*),
//     logged, and sent over mobile data (SIM800L GPRS) with WiFi as the fallback (backend_api.*).
//   - Live first, dead-letter on failure: each record is reported back to the Pi as delivered
//     (ACK) or not (FAIL <reason> <the composed message>). Only failed ones stay in the Pi's
//     SQLite queue; they're replayed DLQ_REPLAY_BATCH at a time once the backend is reachable.
//   - Handles the panic button (panic_button.*) completely outside that loop: unconditional
//     `critical`, an SMS through the SIM800L (works even with no backend), and POST /api/alerts.

#include <string>

#include "ack_policy.h"
#include "backend_api.h"
#include "backend_messages.h"
#include "bluetooth_client.h"
#include "config.h"
#include "credentials.h"
#include "gps_module.h"
#include "panic_button.h"
#include "pi_event_policy.h"
#include "sms_module.h"
#include "wifi_client.h"

namespace {
EpisodeAlerts episodes;           // backend alert id per open episode
PiSeverity liveSeverity = PiSeverity::Unknown;  // the Pi's latest severity (status vigilance)
bool replayStopped = false;       // a replay batch stops at its first failure
unsigned long lastBtPollAt = 0;
unsigned long lastGpsLogAt = 0;
unsigned long lastLinkLogAt = 0;

GpsSnapshot currentGps() {
  GpsSnapshot g;
  g.hasFix = gpsHasFix();
  if (g.hasFix) {
    g.lat = gpsLatitude();
    g.lon = gpsLongitude();
    g.speedKmh = (float)gpsSpeedKmh();
  }
  return g;
}

// When the event happened: the Pi's clock if it was NTP-synced, else GPS time (when this device
// handles it, a second or two later), else nothing (the backend stamps server time).
std::string occurredAt(const PiRecord& r) {
  if (r.hasClockSynced && r.clockSynced && r.createdAtMs > 0) return isoFromEpochMs(r.createdAtMs);
  return gpsUtcIso8601().c_str();
}

void printGps(const GpsSnapshot& g) {
  if (g.hasFix) {
    Serial.print(" | gps ");
    Serial.print(g.lat, 6);
    Serial.print(",");
    Serial.print(g.lon, 6);
    Serial.print(" ");
    Serial.print(g.speedKmh, 0);
    Serial.print(" km/h");
  } else {
    Serial.print(" | gps no fix (");
    Serial.print(gpsSatellites());
    Serial.print(" sats)");
  }
}

// One line per record. Keepalives too: they're what shows the camera's state every 2 s.
void logRecord(const PiRecord& r, const GpsSnapshot& g, bool replay) {
  Serial.print(replay ? "[replay] " : "[pi] ");
  if (replay) {
    Serial.print("(dead letter: ");
    Serial.print(r.dlqError.c_str());
    Serial.print(", attempt ");
    Serial.print(r.dlqAttempts + 1);
    Serial.print(") ");
  }
  if (r.kind == PiRecordKind::RouteStatus) {
    Serial.print("heartbeat");
  } else {
    Serial.print(r.transition ? "" : "keepalive ");
    Serial.print(r.severity.empty() ? "?" : r.severity.c_str());
    if (r.transition && !r.previousSeverity.empty() && r.previousSeverity != r.severity) {
      Serial.print(" (was ");
      Serial.print(r.previousSeverity.c_str());
      Serial.print(")");
    }
    if (!r.reason.empty()) {
      Serial.print(" \"");
      Serial.print(r.reason.c_str());
      Serial.print("\"");
    }
    Serial.print(" class=");
    Serial.print(r.className.c_str());
    if (r.hasProbabilities) {
      Serial.print(" p=[");
      Serial.print(r.pNotDrowsy, 2);
      Serial.print(",");
      Serial.print(r.pDrowsy, 2);
      Serial.print("]");
    }
    if (!r.episode.empty()) {
      Serial.print(" episode=");
      Serial.print(r.episode.c_str());
    }
  }
  if (!replay) printGps(g);
  Serial.println();
}

void bannerOnCritical(const PiRecord& r) {
  PiSeverity s = parsePiSeverity(r.severity);
  if (s == PiSeverity::Unknown) return;
  if (s == PiSeverity::Critical && liveSeverity != PiSeverity::Critical) {
    Serial.println("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!");
    Serial.print("!!! CRITICAL from the camera: ");
    Serial.println(r.reason.empty() ? r.className.c_str() : r.reason.c_str());
    Serial.println("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!");
  } else if (liveSeverity == PiSeverity::Critical && s != PiSeverity::Critical) {
    Serial.println("[pi] critical cleared");
  }
  liveSeverity = s;
}

RecordReport fail(const char* reason, const std::string& body) {
  RecordReport report;
  report.kind = ReportKind::Fail;
  report.reason = reason;
  report.body = body;
  return report;
}

RecordReport ack() { return RecordReport(); }

// Composes the backend message for one record (or reuses a dead letter's), sends it if it can,
// and says what to tell the Pi.
RecordReport handle(const PiRecord& r, bool replay) {
  GpsSnapshot gps = currentGps();
  logRecord(r, gps, replay);
  if (!replay && r.kind == PiRecordKind::Reading) bannerOnCritical(r);

  EventDecision d = decidePiEvent(r, episodes);
  if (d.action == EventAction::Nothing) return ack();

  bool isAlert = d.action == EventAction::RaiseAlert;
  bool reusingDeadLetter = r.isDeadLetter && !r.dlqBody.empty();
  std::string body;
  if (reusingDeadLetter) {
    body = r.dlqBody;  // as composed when it first failed: the original position and time
  } else if (isAlert) {
    AlertFields a;
    a.clientEventId = r.id;
    a.alertType = r.reason.empty() ? r.className : r.reason;
    a.severity = backendSeverity(d.severity);
    a.model = r.model;
    a.hasScores = r.hasProbabilities;
    a.pNotDrowsy = r.pNotDrowsy;
    a.pDrowsy = r.pDrowsy;
    a.relatedAlertId = d.relatedAlertId;
    a.occurredAt = occurredAt(r);
    body = composeAlertBody(a, gps);
  } else if (d.action == EventAction::ResolveAlert) {
    if (d.resolveAlertId.empty()) {
      Serial.println("[backend] Nothing to resolve: this device doesn't know the episode's "
                     "alert (it rebooted, or the alert is still in the Pi's dead-letter queue)");
      return ack();
    }
    std::string when = occurredAt(r);
    if (when.empty()) return fail("no_time", "");  // resolved_at needs a real time
    body = composeResolveBody(when);
  } else {  // PostStatus
    if (!gps.hasFix) {
      Serial.println("[backend] Status skipped: no GPS fix, so no position to report");
      return ack();
    }
    body = composeStatusBody(r.id, backendSeverity(liveSeverity), occurredAt(r), gps);
  }

  Serial.print("[backend] ");
  Serial.print(eventActionName(d.action));
  Serial.print(reusingDeadLetter ? " (recovered message): " : ": ");
  Serial.println(body.c_str());

  BackendBlock block = backendReadiness();
  if (block != BackendBlock::Ready) {
    Serial.print("[backend] Not sent: ");
    Serial.print(backendBlockReason(block));
    Serial.print(" (");
    Serial.print(backendBlockHint(block));
    Serial.println(") -> the Pi keeps it");
    return fail(backendBlockReason(block), body);
  }

  String path;
  std::string toSend = body;
  if (d.action == EventAction::ResolveAlert) {
    path = String("/api/alerts/") + d.resolveAlertId.c_str() + "/resolve";
  } else {
    SendTimeFill f;
    f.routeId = backendActiveRouteId().c_str();
    f.relatedAlertId = d.relatedAlertId;
    f.gps = gps;
    bool usedCurrentGps = false;
    if (!completeBody(toSend, f, isAlert, &usedCurrentGps)) {
      Serial.println("[backend] Not sent: no_gps (no position, then or now) -> the Pi keeps it");
      return fail("no_gps", body);
    }
    if (usedCurrentGps) {
      Serial.println("[backend] No position from the event itself: sending the current one");
    }
    path = isAlert ? String("/api/alerts")
                   : String("/api/routes/") + backendActiveRouteId() + "/status";
  }

  BackendResult res = backendPost(path, toSend.c_str());
  backendNoteStatus(res.status, /*routeScoped=*/d.action != EventAction::ResolveAlert);
  RelayAction action = relayActionFor(res.status, true);
  if (action == RelayAction::Ack) {
    if (isAlert && !r.episode.empty()) {
      int at = res.body.indexOf("\"id_alert\":\"");
      if (at >= 0) {
        int from = at + 12;
        int to = res.body.indexOf('"', from);
        if (to > from) episodes.remember(r.episode, res.body.substring(from, to).c_str());
      }
    } else if (d.action == EventAction::ResolveAlert) {
      episodes.forget(r.episode);
    }
    return ack();
  }
  if (action == RelayAction::AckAndDrop) {
    Serial.print("[backend] Rejected for good (");
    Serial.print(res.status);
    Serial.println("), not kept: retrying can't succeed");
    return ack();
  }
  std::string reason = res.status > 0 ? "http_" + std::to_string(res.status) : "no_network";
  Serial.print("[backend] Not sent: ");
  Serial.print(reason.c_str());
  Serial.println(" -> the Pi keeps it");
  return fail(reason.c_str(), body);
}

RecordReport handleLive(const PiRecord& r) { return handle(r, /*replay=*/false); }

RecordReport handleReplay(const PiRecord& r) {
  if (replayStopped) return RecordReport{ReportKind::Skip, "", ""};
  RecordReport report = handle(r, /*replay=*/true);
  if (report.kind == ReportKind::Fail) replayStopped = true;  // try the rest next time
  return report;
}

// Asks the Pi for CONFIG when the cached credentials are stale (every boot, after a 401, and
// periodically). A failed fetch keeps whatever NVS copy exists.
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
  GpsSnapshot gps = currentGps();

  // Hardware-direct fallback -- fires even with no backend. Never gated on the checks below;
  // this is the point of keeping the SIM800L's SMS at all.
  smsSendPanicAlert(gps.hasFix, gps.lat, gps.lon);

  String id = String("panic-") + String((uint32_t)ESP.getEfuseMac(), HEX) + "-" + millis();
  std::string body = composePanicAlertBody(id.c_str(), gpsUtcIso8601().c_str(), gps);
  Serial.print("[backend] panic alert: ");
  Serial.println(body.c_str());
  BackendBlock block = backendReadiness();
  SendTimeFill f;
  f.routeId = backendActiveRouteId().c_str();
  f.gps = gps;
  if (block != BackendBlock::Ready || !completeBody(body, f, /*isAlert=*/true, nullptr)) {
    Serial.print("[main] Panic SMS sent, but the alert couldn't also be POSTed: ");
    Serial.println(block != BackendBlock::Ready ? backendBlockHint(block) : "no GPS fix");
    return;
  }
  BackendResult res = backendPost("/api/alerts", body.c_str());
  backendNoteStatus(res.status, /*routeScoped=*/true);
}
}  // namespace

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
  if (now - lastLinkLogAt >= PI_BT_LINK_LOG_INTERVAL_MS) {
    lastLinkLogAt = now;
    bluetoothLogStatus();
  }

  if (now - lastBtPollAt < PI_BT_POLL_INTERVAL_MS) return;
  lastBtPollAt = now;

  refreshCredentialsIfStale();

  // Live first: what the camera decided since the last poll.
  int dlqWaiting = 0;
  char request[16];
  snprintf(request, sizeof(request), "PULL %d", PI_BT_PULL_COUNT);
  if (bluetoothExchange(request, handleLive, &dlqWaiting) < 0) return;

  // Then a few dead letters, only when they can actually be delivered now.
  if (dlqWaiting > 0 && backendReadiness() == BackendBlock::Ready) {
    Serial.print("[replay] ");
    Serial.print(dlqWaiting);
    Serial.println(" message(s) in the Pi's dead-letter queue -- sending the oldest");
    replayStopped = false;
    snprintf(request, sizeof(request), "DLQ %d", DLQ_REPLAY_BATCH);
    int ignored = 0;
    bluetoothExchange(request, handleReplay, &ignored);
  }
}
