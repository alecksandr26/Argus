// Argus ESP32 — "Message Sender Orchestrator" (see the root CLAUDE.md and this module's own
// README.md). The only device in the cabin that talks to the cloud backend. The contracts it
// implements are docs/designs/final-tweaks-contracts.md C1 (Bluetooth v2 with the Pi) and C2
// (POST /api/devices/me/report).
//
// Three tasks, one shared state behind one mutex (stateMutex, short critical sections only):
//
//   - loop() (the Arduino loop task): every PI_BT_POLL_INTERVAL_MS, a Bluetooth session with the
//     Pi. `PULL 8`: every record updates the vigilance; a keepalive or non-event is ACKed; an event
//     (C1.5: a medium alert, a critical raise/escalation, a driver OK) is composed with this
//     device's position and TAKEN for the sender. Then `DLQ 1` when the Pi holds dead letters and
//     no live event is pending. Each session also carries the ACK/FAIL of every event the sender
//     finished. No backend call ever happens inside a session. CONFIG (credentials.h) is fetched
//     here too: at boot, after a 401, and every 10 minutes.
//   - gpsTask: drains the NEO-6M and reads it every GPS_READ_INTERVAL_MS into the track buffer (a
//     point per read: the current fix, else the last known position). The last known position
//     is kept in NVS ("lastpos").
//   - senderTask: the only code that touches the modem, WiFi or HTTP. One report per POST: the
//     points since the last report plus at most one event, by priority (live critical > medium >
//     OK > one dead letter); live events retry within their windows, then a critical goes back to
//     the Pi's DLQ and a medium/OK is dropped (sender_policy.h). Mobile data first, WiFi as the
//     fallback, a real deadline per attempt, and a live event pre-empts a lower attempt between
//     AT steps. One [live]/[dlq] line per attempt, and heap + stack high-water marks.
//
// There is no grip sensor yet, so fusion.* stays unused; alerts follow the Pi's own severity.

#include <Preferences.h>

#include <new>
#include <string>
#include <vector>

#include "ack_policy.h"
#include "backend_api.h"
#include "backend_messages.h"
#include "bluetooth_client.h"
#include "config.h"
#include "credentials.h"
#include "gps_module.h"
#include "gps_track.h"
#include "modem.h"
#include "pi_event_policy.h"
#include "pi_protocol.h"
#include "pi_record.h"
#include "pull_session.h"
#include "rtos_lock.h"
#include "sender_policy.h"
#include "sender_step.h"
#include "wifi_client.h"

namespace {

// ---- Shared by the three tasks: guarded by stateMutex ----
SemaphoreHandle_t stateMutex = nullptr;
SenderPolicy sender;
TrackBuffer track;
VigilanceTracker vigilance;
LastKnownPosition lastKnown;
GpsSnapshot latestGps;      // the newest GPS read, for events composed in a Bluetooth session
uint64_t latestGpsEpoch = 0;
uint32_t latestGpsAt = 0;

// NVS "argus-state": the last known position. setup() and then gpsTask only.
Preferences state;

TaskHandle_t loopTaskHandle = nullptr;
TaskHandle_t senderTaskHandle = nullptr;
TaskHandle_t gpsTaskHandle = nullptr;

// ---- The loop task only ----
PiSeverity bannerSeverity = PiSeverity::Unknown;
unsigned long lastBtPollAt = 0;
unsigned long lastLinkLogAt = 0;
EndLine lastEnd;  // the Pi's waiting counts from the last session

// =====================================================================================
// gpsTask
// =====================================================================================

void readGps(uint32_t nowMs) {
  GpsSnapshot g;
  g.hasFix = gpsHasFix();
  if (g.hasFix) {
    g.lat = gpsLatitude();
    g.lon = gpsLongitude();
    g.speedKmh = (float)gpsSpeedKmh();
  }
  uint64_t epoch = gpsEpochSeconds();
  std::string toSave;
  {
    RtosLock lock(stateMutex);
    latestGps = g;
    latestGpsEpoch = epoch;
    latestGpsAt = nowMs;
    if (lastKnown.onRead(g.hasFix, g.lat, g.lon, epoch, nowMs)) toSave = lastKnown.serialize();
    TrackPoint p;
    if (trackPointFor(g, lastKnown, epoch, vigilance.vigilance(nowMs), nowMs, p)) track.add(p);
  }
  // Every GPS_LAST_KNOWN_SAVE_MS with a fix, and at once when it is lost. Outside the lock: an
  // NVS write can take a while.
  if (!toSave.empty()) state.putString("lastpos", toSave.c_str());
}

void gpsTask(void*) {
  uint32_t nextReadAt = millis();
  uint32_t lastLogAt = millis();
  for (;;) {
    gpsPoll();
    uint32_t now = millis();
    if ((int32_t)(now - nextReadAt) >= 0) {
      nextReadAt += GPS_READ_INTERVAL_MS;
      if ((int32_t)(now - nextReadAt) >= 0) nextReadAt = now + GPS_READ_INTERVAL_MS;  // no burst
      readGps(now);
    }
    if (now - lastLogAt >= GPS_LOG_INTERVAL_MS) {
      lastLogAt = now;
      gpsLogStatus();
    }
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

// =====================================================================================
// senderTask
// =====================================================================================

// Asked between AT steps of an attempt: a higher live event pre-empts it (sender_policy.h).
bool preemptCheck() {
  RtosLock lock(stateMutex);
  return sender.shouldPreempt(millis());
}

UBaseType_t stackLeft(TaskHandle_t task) { return task ? uxTaskGetStackHighWaterMark(task) : 0; }

void logHealth() {
  int live = 0;
  bool dlq;
  int outcomes;
  uint32_t lost, dropped;
  RouteKnowledge route;
  {
    RtosLock lock(stateMutex);
    for (int i = 0; i < SenderPolicy::kLiveSlots; i++) live += sender.liveSlot(i).used ? 1 : 0;
    dlq = sender.dlqHeld();
    outcomes = sender.outcomeCount();
    lost = sender.outcomesLost();
    dropped = sender.bodiesDropped();
    route = sender.route();
  }
  Serial.printf(
      "[health] heap free %u B, min ever %u B, largest block %u B | stack left: sender %u B, "
      "loop %u B, gps %u B | held: %d live, %d dlq, %d outcome(s) for the Pi (%u lost, %u bodies "
      "cut) | route %s\n",
      (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMinFreeHeap(), (unsigned)ESP.getMaxAllocHeap(),
      (unsigned)stackLeft(senderTaskHandle), (unsigned)stackLeft(loopTaskHandle),
      (unsigned)stackLeft(gpsTaskHandle), live, dlq ? 1 : 0, outcomes, (unsigned)lost,
      (unsigned)dropped,
      route == RouteKnowledge::Active ? "active" : route == RouteKnowledge::None ? "none" : "unknown");
}

PreparedReport rep;  // static: the sender task's one report in the making

void senderOnce() {
  bool due;
  {
    RtosLock lock(stateMutex);
    due = prepareReport(sender, track, millis(), rep);
  }
  if (!due) {
    uint32_t waitMs = rep.plan.kind == PlanKind::Wait ? rep.plan.waitMs : 100;
    if (waitMs > 100) waitMs = 100;  // stay responsive to new events
    vTaskDelay(pdMS_TO_TICKS(waitMs > 0 ? waitMs : 1));
    return;
  }
  if (rep.hasEvent) {
    Serial.print(rep.dlq ? "[dlq] event: " : "[live] event: ");
    Serial.println(rep.body.c_str());
  }

  uint32_t startedAt = millis();
  ReportSend sent = backendPostReport(rep.body, rep.plan.retry, preemptCheck);
  uint32_t endedAt = millis();
  ReportReaction reaction;
  if (!sent.preempted) {
    const char* body = sent.body.c_str();
    reaction = reactToReport(sent.status, body ? std::string(body) : std::string(), rep.hasEvent,
                             sent.reason);
  }
  if (reaction.recredential) {
    Serial.println("[report] 401 -- device key rejected, re-fetching CONFIG from the Pi");
    credentialsMarkStale();
  }
  ReportResult result;
  {
    RtosLock lock(stateMutex);
    result = finishReport(sender, track, rep, sent.preempted, reaction, endedAt);
  }
  Serial.println(reportLogLine(rep, sent.via, endedAt - startedAt, result).c_str());
}

void senderTask(void*) {
  uint32_t lastHealthAt = millis();
  for (;;) {
    try {
      backendPoll();  // WiFi upkeep, a late +HTTPACTION
      if (millis() - lastHealthAt >= TASK_HEALTH_LOG_INTERVAL_MS) {
        lastHealthAt = millis();
        logHealth();
      }
      senderOnce();
    } catch (const std::bad_alloc&) {
      // Never abort-and-reboot on a full heap (commit f4da937): end the attempt as not made.
      {
        RtosLock lock(stateMutex);
        if (sender.inFlight()) sender.endAttempt(AttemptEnd::Preempted, "", millis());
      }
      Serial.printf("[sender] Out of memory (free %u B, largest %u B) -- retrying shortly\n",
                    (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMaxAllocHeap());
      vTaskDelay(pdMS_TO_TICKS(1000));
    }
    vTaskDelay(1);
  }
}

// =====================================================================================
// The loop task: Bluetooth sessions with the Pi
// =====================================================================================

HerePosition hereNow(uint32_t nowMs) {  // under stateMutex
  uint64_t epochNow = latestGpsEpoch ? latestGpsEpoch + (nowMs - latestGpsAt) / 1000 : 0;
  HerePosition h;
  h.position = eventPosition(latestGps, lastKnown, epochNow, nowMs);
  h.hasSpeed = latestGps.hasFix;
  h.speedKmh = latestGps.speedKmh;
  return h;
}

void bannerOnCritical(const PiRecord& r) {
  PiSeverity s = parsePiSeverity(r.severity);
  if (s == PiSeverity::Unknown) return;
  if (s == PiSeverity::Critical && bannerSeverity != PiSeverity::Critical) {
    Serial.println("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!");
    Serial.print("!!! CRITICAL from the camera: ");
    Serial.println(r.reason.empty() ? r.className.c_str() : r.reason.c_str());
    Serial.println("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!");
  } else if (bannerSeverity == PiSeverity::Critical && s != PiSeverity::Critical) {
    Serial.println("[pi] critical cleared");
  }
  bannerSeverity = s;
}

const char* actionName(const RecordDecision& d) {
  switch (d.action) {
    case RecordAction::Ack: return "ACK";
    case RecordAction::Take: return d.take == TakeResult::AlreadyHeld ? "TAKEN (held already)"
                                                                       : "TAKEN";
    case RecordAction::FailTooOld: return "FAIL too_old";
    case RecordAction::FailInvalid: return "FAIL invalid";
    case RecordAction::Leave:
      return d.take == TakeResult::Full              ? "left on the Pi (sender full)"
             : d.take == TakeResult::AlreadyFinished ? "left (its outcome goes this session)"
                                                     : "left on the Pi";
  }
  return "?";
}

// One line per record. Keepalives too: they're what shows the camera's state every 2 s.
void logRecord(const PiRecord& r, ParseOutcome parsed, const RecordDecision& d, bool dlq) {
  Serial.print(dlq ? "[dlq] " : "[pi] ");
  if (parsed == ParseOutcome::Drop) {
    Serial.print(r.id.empty() ? "unreadable record with no id" : "unusable record ");
    Serial.print(r.id.c_str());
  } else {
    if (dlq) {
      Serial.printf("(dead letter: %s, %d failed attempt(s), queued %.0f s) ", r.dlqError.c_str(),
                    r.dlqAttempts, r.dlqQueuedS);
    }
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
    if (r.hasProbabilities) Serial.printf(" p=[%.2f,%.2f]", r.pNotDrowsy, r.pDrowsy);
    if (!r.episode.empty()) {
      Serial.print(" episode=");
      Serial.print(r.episode.c_str());
    }
    if (r.hasAgeS) Serial.printf(" age=%.1fs", r.ageS);
    if (d.kind != PiEventKind::None) {
      Serial.print(" => ");
      Serial.print(piEventKindName(d.kind));
    }
  }
  Serial.print(" -> ");
  Serial.println(actionName(d));
}

void logSession(bool dlq, size_t records, const SessionReport& report, bool gotOk,
                const SessionSummary& summary, const EndLine& end) {
  bool quiet = gotOk && report.taken.empty() && report.fails.empty() && report.leftFull == 0 &&
               report.leftOther == 0 && report.outcomesIncluded == 0;
  if (quiet) return;  // keepalives only: their own lines said it all
  Serial.printf("[bt] %s: %u record(s) -> %u ACK, %u TAKEN, %u FAIL, left %d (%d sender full); "
                "%d outcome(s) | END dlq=%d outbox=%d | ",
                dlq ? "DLQ" : "PULL", (unsigned)records, (unsigned)report.acks.size(),
                (unsigned)report.taken.size(), (unsigned)report.fails.size(),
                report.leftFull + report.leftOther, report.leftFull, report.outcomesIncluded,
                end.dlqWaiting, end.outboxWaiting);
  if (gotOk) {
    Serial.printf("OK acked=%d failed=%d leased=%d dlq=%d outbox=%d\n", summary.acked,
                  summary.failed, summary.leased, summary.dlq, summary.outbox);
  } else {
    Serial.println("no OK from the Pi: the outcomes go again next session");
  }
}

// One PULL or DLQ session. False when it didn't complete (no link, protocol error, no OK).
bool runSession(bool dlq) {
  try {
    std::vector<std::string> lines;
    EndLine end;
    if (!bluetoothOpenSession(dlq ? dlqRequest() : pullRequest(PI_BT_PULL_COUNT), lines, end)) {
      return false;
    }
    lastEnd = end;
    SessionReport report;
    for (const std::string& line : lines) {
      PiRecord r;
      ParseOutcome parsed = parsePiRecord(line, r);
      RecordDecision d;
      {
        RtosLock lock(stateMutex);
        uint32_t now = millis();
        d = decidePulledRecord(parsed, r, dlq, sender, vigilance, hereNow(now), now);
      }
      addRecordDecision(report, r.id, d);
      if (!dlq && parsed == ParseOutcome::Ok) bannerOnCritical(r);
      logRecord(r, parsed, d, dlq);
    }
    {
      RtosLock lock(stateMutex);
      addPendingOutcomes(report, sender);
    }
    std::vector<ScriptLine> script = buildReportScript(report.acks, report.fails, report.taken);
    SessionSummary summary;
    bool gotOk = bluetoothCloseSession(script, summary);
    if (gotOk) {
      RtosLock lock(stateMutex);
      sender.dropOutcomesBefore(report.outcomeSeqEnd);
    }
    logSession(dlq, lines.size(), report, gotOk, summary, end);
    return gotOk;
  } catch (const std::bad_alloc&) {
    bluetoothDisconnect();
    Serial.printf("[bt] Out of memory handling a session (free %u B, largest %u B) -- the Pi "
                  "serves it again\n",
                  (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMaxAllocHeap());
    return false;
  }
}

// Asks the Pi for CONFIG when the cached credentials are stale (every boot, after a 401, and
// periodically). A failed fetch keeps whatever NVS copy exists.
void refreshCredentialsIfStale() {
  if (!credentialsStale()) return;
  DeviceCredentials fresh;
  switch (bluetoothFetchConfig(fresh)) {
    case ConfigLineResult::Ok:
      credentialsApply(fresh);
      {
        RtosLock lock(stateMutex);
        sender.forgetRoute();  // new credentials, maybe another truck: unknown until a 200
      }
      break;
    case ConfigLineResult::NoConfig:
      credentialsNoneOnPi();
      break;
    case ConfigLineResult::Malformed:
      break;  // still stale: retried on the next poll
  }
}

// Old NVS keys of the v1 firmware: the open episodes' alert ids and the panic queue.
void removeStaleNvsKeys() {
  for (const char* key : {"episodes", "panics"}) {
    if (state.isKey(key)) {
      state.remove(key);
      Serial.printf("[main] Removed the old NVS key \"%s\" (v1 firmware)\n", key);
    }
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println();
  Serial.println("==== Argus ESP32 -- Message Sender Orchestrator (Bluetooth v2, reports) ====");
  if (BENCH_FAKE_GPS) {
    Serial.println("[main] WARNING: BENCH_FAKE_GPS is on -- reporting a fixed bench position");
  }
  stateMutex = xSemaphoreCreateMutex();
  loopTaskHandle = xTaskGetCurrentTaskHandle();

  state.begin("argus-state", /*readOnly=*/false);
  removeStaleNvsKeys();
  String saved = state.getString("lastpos", "");
  if (saved.length() > 0 && lastKnown.deserialize(saved.c_str()) && lastKnown.known()) {
    Serial.println("[main] Restored the last known position from NVS (used once GPS time is back)");
  }

  gpsBegin();
  modemBegin();        // the SIM800L carries mobile data only
  credentialsBegin();  // before wifiBegin(): the WiFi to join is in the NVS cache
  wifiBegin();
  bluetoothClientBegin();

  // Both on the Arduino core (1), like loop(); gpsTask a step higher so its 1 s reads stay on
  // time while a TLS handshake or a Bluetooth session keeps the others busy.
  xTaskCreatePinnedToCore(gpsTask, "gps", GPS_TASK_STACK, nullptr, 2, &gpsTaskHandle, 1);
  xTaskCreatePinnedToCore(senderTask, "sender", SENDER_TASK_STACK, nullptr, 1, &senderTaskHandle,
                          1);
}

void loop() {
  unsigned long now = millis();
  if (now - lastLinkLogAt >= PI_BT_LINK_LOG_INTERVAL_MS) {
    lastLinkLogAt = now;
    bluetoothLogStatus();
  }
  if (now - lastBtPollAt < PI_BT_POLL_INTERVAL_MS) {
    vTaskDelay(pdMS_TO_TICKS(20));
    return;
  }
  lastBtPollAt = now;

  refreshCredentialsIfStale();

  // Live first: what the camera decided since the last poll (and the finished events' outcomes).
  if (!runSession(/*dlq=*/false)) return;

  // Then one dead letter, only with no live event pending and credentials to send it with (C1.8).
  bool wantsDlq;
  {
    RtosLock lock(stateMutex);
    wantsDlq = sender.wantsDlq();
  }
  if (lastEnd.dlqWaiting > 0 && wantsDlq && credentialsReady()) {
    Serial.printf("[dlq] %d dead letter(s) on the Pi -- asking for one\n", lastEnd.dlqWaiting);
    runSession(/*dlq=*/true);
  }
}
