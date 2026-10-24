// Argus ESP32 — "Message Sender Orchestrator" (see the root CLAUDE.md and this module's own
// README.md). The only device in the cabin that talks to the cloud backend. The contracts it
// implements are docs/designs/final-tweaks-contracts.md C1 (Bluetooth v2 with the Pi) and C2
// (POST /api/devices/me/report), and docs/designs/grip-alarm-contracts.md (the grip sensor, the
// cabin buzzer and the critical-alert SMS).
//
// Four tasks, one shared state behind one mutex (stateMutex, short critical sections only):
//
//   - loop() (the Arduino loop task): every PI_BT_POLL_INTERVAL_MS, a Bluetooth session with the
//     Pi. `PULL 8`: every record updates the vigilance; a keepalive or non-event is ACKed; an event
//     (C1.5: a medium alert, a critical raise/escalation, a driver OK) is composed with this
//     device's position and TAKEN for the sender. Then `DLQ 1` when the Pi holds dead letters and
//     no live event is pending. Each session also carries the ACK/FAIL of every event the sender
//     finished. No backend call ever happens inside a session. CONFIG (credentials.h) is fetched
//     here too: at boot, after a 401, and every 10 minutes. A live critical it takes also starts
//     the vision alarm (not for no_face) and queues one SMS per episode; a driver OK stops the
//     vision alarm (alarm_policy.h, sms_policy.h).
//   - gpsTask: drains the NEO-6M and reads it every GPS_READ_INTERVAL_MS into the track buffer (a
//     point per read: the current fix, else the last known position). The last known position
//     is kept in NVS ("lastpos").
//   - gripTask: every GRIP_SAMPLE_INTERVAL_MS reads the four wheel sensors (grip_policy.h): 3 s
//     without pressure starts a grip episode (the grip alarm, a live critical "no_grip" for the
//     sender, one SMS), 300 ms of pressure ends it (one live OK). It also drives the buzzer:
//     grip alarm || vision alarm, with the vision alarm's 5-minute cap.
//   - senderTask: the only code that touches the modem, WiFi or HTTP. One report per POST: the
//     points since the last report plus at most one event, by priority (live critical > medium >
//     OK > one dead letter; the grip events rank as live ones of their tier); live events retry
//     within their windows, then a Pi critical goes back to the Pi's DLQ, a medium/OK is dropped,
//     and a grip event is dropped (sender_policy.h). Mobile data first, WiFi as the fallback, a
//     real deadline per attempt, and a live event pre-empts a lower attempt between AT steps. One
//     [live]/[dlq] line per attempt, and heap + stack high-water marks. Between attempts it sends
//     the waiting SMS (sms_modem.h), never in the middle of an HTTP request.
//
// fusion.* stays unused: camera alerts follow the Pi's own severity, and the grip sensor is its
// own critical source (grip-alarm contracts C2).

#include <Preferences.h>

#include <new>
#include <string>
#include <vector>

#include "ack_policy.h"
#include "alarm_policy.h"
#include "backend_api.h"
#include "backend_messages.h"
#include "bluetooth_client.h"
#include "buzzer.h"
#include "cabin_signals.h"
#include "config.h"
#include "credentials.h"
#include "gps_module.h"
#include "gps_track.h"
#include "grip_policy.h"
#include "grip_sensor.h"
#include "modem.h"
#include "pi_event_policy.h"
#include "pi_protocol.h"
#include "pi_record.h"
#include "pull_session.h"
#include "rtos_lock.h"
#include "sender_policy.h"
#include "sender_step.h"
#include "sms_modem.h"
#include "sms_policy.h"
#include "wifi_client.h"

namespace {

// ---- Shared by the four tasks: guarded by stateMutex ----
SemaphoreHandle_t stateMutex = nullptr;
SenderPolicy sender;
TrackBuffer track;
VigilanceTracker vigilance;
LastKnownPosition lastKnown;
GpsSnapshot latestGps;      // the newest GPS read, for events composed in a Bluetooth session
uint64_t latestGpsEpoch = 0;
uint32_t latestGpsAt = 0;
AlarmPolicy cabinAlarm;     // the buzzer's decision: grip alarm || vision alarm
SmsPolicy smsQueue;         // the critical-alert SMS waiting for the sender task

// NVS "argus-state": the last known position. setup() and then gpsTask only.
Preferences state;

TaskHandle_t loopTaskHandle = nullptr;
TaskHandle_t senderTaskHandle = nullptr;
TaskHandle_t gpsTaskHandle = nullptr;
TaskHandle_t gripTaskHandle = nullptr;

// ---- The grip task only ----
GripPolicy gripRule;
uint32_t bootTag = 0;  // esp_random() at boot (set before the tasks start): grip event ids

// ---- The sender task only ----
bool smsWaitLogged = false;
unsigned long smsWaitLoggedAt = 0;

// ---- The loop task only ----
PiSeverity bannerSeverity = PiSeverity::Unknown;
unsigned long lastBtPollAt = 0;
unsigned long lastLinkLogAt = 0;
EndLine lastEnd;  // the Pi's waiting counts from the last session

// Where the truck is right now, for an event composed now (a Pi record in a Bluetooth session, a
// grip episode): the current fix, else the last known position, else unknown. Under stateMutex.
HerePosition hereNow(uint32_t nowMs) {
  uint64_t epochNow = latestGpsEpoch ? latestGpsEpoch + (nowMs - latestGpsAt) / 1000 : 0;
  HerePosition h;
  h.position = eventPosition(latestGps, lastKnown, epochNow, nowMs);
  h.hasSpeed = latestGps.hasFix;
  h.speedKmh = latestGps.speedKmh;
  return h;
}

// Logs the grip events the sender gave up on (sender_policy.h takeLocalDrop()): copied under the
// lock, printed outside it. Grip events have no DLQ, so this line is all that is left of them.
void logLocalDrops() {
  LocalDrop drops[SenderPolicy::kLocalDropSlots];
  int n = 0;
  {
    RtosLock lock(stateMutex);
    while (n < SenderPolicy::kLocalDropSlots && sender.takeLocalDrop(drops[n])) n++;
  }
  for (int i = 0; i < n; i++) {
    const LocalDrop& d = drops[i];
    Serial.printf("[live] %s %s dropped: ", eventTierName(d.tier), d.clientEventId.c_str());
    if (d.why == "retry_window") {
      Serial.printf("not delivered within its retry window (%d attempt(s)%s%s; grip events have "
                    "no DLQ)\n",
                    d.attempts, d.lastError.empty() ? "" : ", last: ", d.lastError.c_str());
    } else if (d.why == "replaced") {
      Serial.println("still unsent when the next grip episode's event came");
    } else {
      Serial.println("the previous grip event was still on its way");
    }
  }
}

// What a queued SMS means for the log (sms_policy.h); quiet for duplicates and non-criticals.
void logSmsQueued(const SmsQueueResult& r, const std::string& episode) {
  if (r.evicted) {
    Serial.printf("[sms] Queue full (%d): dropped the waiting SMS for episode %s\n",
                  SmsPolicy::kSlots, r.evictedEpisode.c_str());
  }
  switch (r.result) {
    case SmsQueued::Queued:
      Serial.printf("[sms] SMS queued for episode %s\n", episode.c_str());
      break;
    case SmsQueued::NoPhone:
      Serial.printf("[sms] No SMS alert phone set (ARGUS_ALERT_PHONE on the Pi) -- no SMS for "
                    "episode %s\n",
                    episode.c_str());
      break;
    case SmsQueued::NoFace:
      Serial.println("[sms] no_face critical: no SMS (SMS_ON_NO_FACE is 0)");
      break;
    case SmsQueued::Duplicate:
    case SmsQueued::Ignored:
      break;
  }
}

// =====================================================================================
// gripTask: the wheel sensors and the buzzer
// =====================================================================================

void logGripReadings(const int* raw, bool pressure) {
  Serial.print("[grip]");
  for (int i = 0; i < GRIP_SENSOR_COUNT; i++) Serial.printf(" S%d=%d", i + 1, raw[i]);
  Serial.printf(" (threshold %d) -> %s\n", GRIP_PRESSURE_THRESHOLD,
                pressure ? "pressure" : "no pressure");
}

// A grip episode started or ended: its event for the sender (C3), the grip alarm, and on a start
// its SMS (C5).
void onGripChange(GripChange change, uint32_t nowMs) {
  bool started = change == GripChange::EpisodeStarted;
  uint32_t n = gripRule.episode();
  std::string episode = gripEpisodeId(bootTag, n);
  bool hasPhone = started && credentialsHasAlertPhone();  // its own lock, never inside stateMutex
  TakeResult took = TakeResult::Queued;
  SmsQueueResult queued;
  {
    RtosLock lock(stateMutex);
    HerePosition here = hereNow(nowMs);
    ReportEvent ev = started
                         ? gripAlertEvent(bootTag, n, here.position, here.hasSpeed, here.speedKmh)
                         : gripOkEvent(bootTag, n, here.position, here.hasSpeed, here.speedKmh);
    took = sender.takeLocal(ev, nowMs);
    cabinAlarm.setGripAlarm(started);
    if (started) queued = smsQueue.onGripEpisode(episode, here.position, hasPhone, nowMs);
  }
  const char* queuedText = took == TakeResult::Full ? "dropped (see below)" : "queued";
  if (started) {
    Serial.println("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!");
    Serial.printf("!!! No pressure on the steering wheel for %.1f s -- episode %s\n",
                  GRIP_ALERT_MS / 1000.0, episode.c_str());
    Serial.println("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!");
    Serial.printf("[grip] Grip alarm on; critical no_grip for the backend %s\n", queuedText);
  } else {
    Serial.printf("[grip] Pressure back -- episode %s over: grip alarm off; OK for the backend %s\n",
                  episode.c_str(), queuedText);
  }
  logLocalDrops();
  if (started) logSmsQueued(queued, episode);
}

// The buzzer pin follows the alarm decision: the vision alarm's safety cap first, then
// grip || vision.
void applyBuzzer(uint32_t nowMs) {
  bool capped, buzz, grip, vision;
  {
    RtosLock lock(stateMutex);
    capped = cabinAlarm.tick(nowMs);
    buzz = cabinAlarm.buzzer();
    grip = cabinAlarm.gripAlarm();
    vision = cabinAlarm.visionAlarm();
  }
  if (capped) {
    Serial.printf("[alarm] Vision alarm off: no driver OK from the Pi in %lu s (safety cap)\n",
                  (unsigned long)(VISION_ALARM_MAX_MS / 1000));
  }
  if (buzz == buzzerIsOn()) return;
  buzzerSet(buzz);
  if (buzz) {
    Serial.printf("[alarm] Buzzer ON (%s)\n", grip && vision ? "hands off the wheel + camera"
                                              : grip         ? "hands off the wheel"
                                                             : "camera critical");
  } else {
    Serial.println("[alarm] Buzzer off");
  }
}

void gripTask(void*) {
  uint32_t lastLogAt = millis();
  bool loggedOnce = false;
  for (;;) {
    int raw[GRIP_SENSOR_COUNT];
    gripSensorRead(raw);
    uint32_t now = millis();
    bool pressure = gripHasPressure(raw, GRIP_SENSOR_COUNT);
    if (!loggedOnce || now - lastLogAt >= GRIP_LOG_INTERVAL_MS) {  // not every sample
      loggedOnce = true;
      lastLogAt = now;
      logGripReadings(raw, pressure);
    }
    bool moving;
    {
      RtosLock lock(stateMutex);
      moving = gripIsMoving(latestGps.hasFix, latestGps.speedKmh);
    }
    GripChange change = gripRule.onSample(pressure, moving, now);
    if (change != GripChange::None) onGripChange(change, now);
    applyBuzzer(now);
    vTaskDelay(pdMS_TO_TICKS(GRIP_SAMPLE_INTERVAL_MS));
  }
}

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
  int live = 0, local = 0, smsWaiting = 0;
  bool dlq, buzz;
  int outcomes;
  uint32_t lost, dropped;
  RouteKnowledge route;
  {
    RtosLock lock(stateMutex);
    for (int i = 0; i < SenderPolicy::kAllLiveSlots; i++) {
      if (!sender.liveSlot(i).used) continue;
      if (sender.liveSlot(i).local) {
        local++;
      } else {
        live++;
      }
    }
    dlq = sender.dlqHeld();
    outcomes = sender.outcomeCount();
    lost = sender.outcomesLost();
    dropped = sender.bodiesDropped();
    route = sender.route();
    for (int i = 0; i < SmsPolicy::kSlots; i++) smsWaiting += smsQueue.slot(i).used ? 1 : 0;
    buzz = cabinAlarm.buzzer();
  }
  Serial.printf(
      "[health] heap free %u B, min ever %u B, largest block %u B | stack left: sender %u B, "
      "loop %u B, gps %u B, grip %u B | held: %d live + %d grip, %d dlq, %d outcome(s) for the "
      "Pi (%u lost, %u bodies cut) | route %s | %d SMS waiting | buzzer %s\n",
      (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMinFreeHeap(), (unsigned)ESP.getMaxAllocHeap(),
      (unsigned)stackLeft(senderTaskHandle), (unsigned)stackLeft(loopTaskHandle),
      (unsigned)stackLeft(gpsTaskHandle), (unsigned)stackLeft(gripTaskHandle), live, local,
      dlq ? 1 : 0, outcomes, (unsigned)lost, (unsigned)dropped,
      route == RouteKnowledge::Active ? "active" : route == RouteKnowledge::None ? "none" : "unknown",
      smsWaiting, buzz ? "on" : "off");
}

// The waiting SMS (sms_policy.h), one per call, between report attempts: drops what waited too
// long, then sends the oldest due one if the modem is on the GSM network (no APN needed).
void serviceSms() {
  SmsMessage expired[SmsPolicy::kSlots];
  int nExpired = 0;
  SmsMessage msg;
  bool have = false;
  {
    RtosLock lock(stateMutex);
    uint32_t now = millis();
    while (nExpired < SmsPolicy::kSlots && smsQueue.expireOne(now, expired[nExpired])) nExpired++;
    int i = smsQueue.due(now);
    if (i >= 0) {
      msg = smsQueue.slot(i);
      have = true;
    }
  }
  for (int i = 0; i < nExpired; i++) {
    Serial.printf("[sms] Gave up on the SMS for episode %s: still unsent %lu s after it was queued "
                  "(%d failed attempt(s))\n",
                  expired[i].episode.c_str(), (unsigned long)(SMS_PENDING_MAX_MS / 1000),
                  expired[i].attempts);
  }
  if (!have) {
    smsWaitLogged = false;
    return;
  }

  String phone, vehicle;
  if (!credentialsSmsTarget(phone, vehicle)) {  // the Pi's CONFIG dropped the phone meanwhile
    {
      RtosLock lock(stateMutex);
      smsQueue.drop(msg.episode.str());
    }
    Serial.printf("[sms] No SMS alert phone any more -- dropping the SMS for episode %s\n",
                  msg.episode.c_str());
    return;
  }
  int stat = modemRegistrationCached(MODEM_REG_CACHE_MS);
  if (!modemStatRegistered(stat)) {
    if (!smsWaitLogged || millis() - smsWaitLoggedAt >= SMS_WAIT_LOG_INTERVAL_MS) {
      smsWaitLogged = true;
      smsWaitLoggedAt = millis();
      Serial.printf("[sms] Waiting for the GSM network to send the SMS for episode %s: %s\n",
                    msg.episode.c_str(), modemRegistrationText(stat));
    }
    return;  // it waits (not an attempt), up to SMS_PENDING_MAX_MS
  }
  smsWaitLogged = false;

  std::string text = composeSmsText(msg.alertType.str(), vehicle.c_str(), msg.position);
  Serial.printf("[sms] -> %s (attempt %d/%d): %s\n", maskPhone(phone).c_str(), msg.attempts + 1,
                SMS_MAX_ATTEMPTS, text.c_str());
  String error;
  uint32_t startedAt = millis();
  bool sent = smsSendText(phone, String(text.c_str()), error);
  uint32_t endedAt = millis();
  if (!sent) modemForgetRegistration();  // check the network again before the next try
  SmsAttemptEnd end;
  {
    RtosLock lock(stateMutex);
    end = smsQueue.attemptEnded(msg.episode.str(), sent, endedAt);
  }
  float secs = (endedAt - startedAt) / 1000.0f;
  switch (end) {
    case SmsAttemptEnd::Sent:
      Serial.printf("[sms] Sent for episode %s in %.1f s\n", msg.episode.c_str(), secs);
      break;
    case SmsAttemptEnd::Retry:
      Serial.printf("[sms] Failed for episode %s after %.1f s (%s) -- retrying in %lu s\n",
                    msg.episode.c_str(), secs, error.c_str(),
                    (unsigned long)(SMS_RETRY_GAP_MS / 1000));
      break;
    case SmsAttemptEnd::GaveUp:
      Serial.printf("[sms] Failed for episode %s (%s) -- giving up after %d attempts\n",
                    msg.episode.c_str(), error.c_str(), SMS_MAX_ATTEMPTS);
      break;
    case SmsAttemptEnd::Gone:
      Serial.printf("[sms] %s for episode %s (it had already left the queue)\n",
                    sent ? "Sent" : "Failed", msg.episode.c_str());
      break;
  }
}

PreparedReport rep;  // static: the sender task's one report in the making

// backend_api calls this right before AT+HTTPDATA / http.POST: the body, with every age_s measured
// now (C2.1 [sync 3]). Reads only `rep`, the sender task's own copy: no lock.
std::string composeAtSend(uint32_t nowMs) {
  std::string body = composePreparedReport(rep, nowMs);
  if (rep.hasEvent) {
    Serial.print(rep.dlq ? "[dlq] event: " : "[live] event: ");
    Serial.println(body.c_str());
  }
  return body;
}

void senderOnce() {
  bool due;
  {
    RtosLock lock(stateMutex);
    due = prepareReport(sender, track, millis(), rep);
  }
  logLocalDrops();  // grip events whose window prepareReport's expire() just closed
  if (!due) {
    uint32_t waitMs = rep.plan.kind == PlanKind::Wait ? rep.plan.waitMs : 100;
    if (waitMs > 100) waitMs = 100;  // stay responsive to new events
    vTaskDelay(pdMS_TO_TICKS(waitMs > 0 ? waitMs : 1));
    return;
  }

  uint32_t startedAt = millis();
  ReportSend sent = backendPostReport(composeAtSend, rep.plan.retry, preemptCheck);
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
      serviceSms();  // between report attempts: never inside an HTTP request
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

// What cabinSignalsFor() (cabin_signals.h) did with a pulled record: the vision alarm and the SMS.
void logCabinSignals(const PiRecord& r, const CabinSignals& s) {
  const std::string& episode = eventEpisode(r);
  switch (s.vision) {
    case VisionChange::On:
      Serial.printf("[alarm] Vision alarm ON: \"%s\" (episode %s)\n", eventAlertType(r).c_str(),
                    episode.c_str());
      break;
    case VisionChange::Off:
      Serial.printf("[alarm] Vision alarm off: the driver is OK (episode %s)\n", episode.c_str());
      break;
    case VisionChange::NoFace:
      Serial.println("[alarm] no_face critical: no buzzer for a driver out of view");
      break;
    case VisionChange::None:
      break;
  }
  logSmsQueued(s.sms, episode);
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
    bool hasPhone = credentialsHasAlertPhone();  // its own lock, never inside stateMutex
    SessionReport report;
    for (const std::string& line : lines) {
      PiRecord r;
      ParseOutcome parsed = parsePiRecord(line, r);
      RecordDecision d;
      CabinSignals signals;
      {
        RtosLock lock(stateMutex);
        uint32_t now = millis();
        HerePosition here = hereNow(now);
        d = decidePulledRecord(parsed, r, dlq, sender, vigilance, here, now);
        // The hook: a live critical taken here starts the vision alarm and queues its SMS; a
        // driver OK stops the vision alarm.
        signals = cabinSignalsFor(r, parsed, d, dlq, here.position, hasPhone, cabinAlarm,
                                  smsQueue, now);
      }
      addRecordDecision(report, r.id, d);
      if (!dlq && parsed == ParseOutcome::Ok) bannerOnCritical(r);
      logRecord(r, parsed, d, dlq);
      logCabinSignals(r, signals);
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
      // Only a new truck, key or backend makes the known route unknown again (until a 200); the
      // 10-minute refresh with the same credentials, or a new WiFi/APN, keeps it.
      if (credentialsApply(fresh)) {
        RtosLock lock(stateMutex);
        sender.forgetRoute();
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
  buzzerBegin();  // the pin driven off before anything else can take time
  stateMutex = xSemaphoreCreateMutex();
  loopTaskHandle = xTaskGetCurrentTaskHandle();

  state.begin("argus-state", /*readOnly=*/false);
  removeStaleNvsKeys();
  String saved = state.getString("lastpos", "");
  if (saved.length() > 0 && lastKnown.deserialize(saved.c_str()) && lastKnown.known()) {
    Serial.println("[main] Restored the last known position from NVS (used once GPS time is back)");
  }

  gpsBegin();
  gripSensorBegin();
  modemBegin();        // mobile data and the SMS; it registers on GSM with or without an APN
  credentialsBegin();  // before wifiBegin(): the WiFi to join is in the NVS cache
  wifiBegin();
  bluetoothClientBegin();

  // With the Bluetooth radio on, esp_random() is a true random number: grip event ids
  // ("grip-<boot>-<n>", contracts C3) never repeat across reboots.
  bootTag = esp_random();
  Serial.printf("[grip] This boot's grip episodes: grip-%08lx-<n>\n", (unsigned long)bootTag);

  // All on the Arduino core (1), like loop(); gpsTask and gripTask a step higher so the 1 s GPS
  // reads and the 100 ms wheel samples (and the buzzer) stay on time while a TLS handshake or a
  // Bluetooth session keeps the others busy.
  xTaskCreatePinnedToCore(gpsTask, "gps", GPS_TASK_STACK, nullptr, 2, &gpsTaskHandle, 1);
  xTaskCreatePinnedToCore(gripTask, "grip", GRIP_TASK_STACK, nullptr, 2, &gripTaskHandle, 1);
  xTaskCreatePinnedToCore(senderTask, "sender", SENDER_TASK_STACK, nullptr, 1, &senderTaskHandle,
                          1);
  logHealth();  // the heap left once everything is up, before the first Bluetooth connect
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
