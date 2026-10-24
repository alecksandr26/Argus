// The Pi-record hook (grip-alarm contracts C4, C5) end to end: real record lines through
// parsePiRecord() -> decidePulledRecord() -> cabinSignalsFor(), as the sketch's Bluetooth session
// runs them: a live critical taken here sounds (unless no_face) and queues one SMS, a re-pulled
// row or a DLQ replay changes nothing, a driver OK stops the alarm.
// SOURCES: cabin_signals.cpp alarm_policy.cpp sms_policy.cpp pull_session.cpp sender_policy.cpp backend_messages.cpp pi_event_policy.cpp pi_record.cpp gps_track.cpp pi_protocol.cpp
// NEEDS: arduinojson
#include <ArduinoJson.h>

#include <string>

#include "cabin_signals.h"
#include "check.h"

static std::string line(const char* id, const char* severity, const char* previous,
                        const char* reason = "eyes_closed", const char* episode = "ep1",
                        const char* extra = "") {
  return std::string(R"({"id":")") + id +
         R"(","kind":"drowsiness","level":2,"created_at_ms":1,"source_id":"c","age_s":0.4,)" +
         R"("payload":{"class_name":"Drowsy","probabilities":[0.1,0.9],"transition":true,)" +
         R"("severity":")" + severity + R"(","previous_severity":")" + previous +
         R"(","episode":")" + episode + R"(","reason":")" + reason +
         R"(","model":"parts-rules-v1"})" + extra + "}";
}

static HerePosition here() {
  HerePosition h;
  h.position.known = true;
  h.position.lat = 20.659712;
  h.position.lon = -103.349612;
  h.hasSpeed = true;
  h.speedKmh = 60;
  return h;
}

struct Cabin {
  SenderPolicy sender;
  VigilanceTracker vigilance;
  AlarmPolicy alarm;
  SmsPolicy sms;
  bool hasPhone = true;

  CabinSignals feed(const std::string& text, uint32_t now, bool dlq = false,
                    RecordDecision* decision = nullptr) {
    PiRecord r;
    ParseOutcome parsed = parsePiRecord(text, r);
    HerePosition h = here();
    RecordDecision d = decidePulledRecord(parsed, r, dlq, sender, vigilance, h, now);
    if (decision) *decision = d;
    return cabinSignalsFor(r, parsed, d, dlq, h.position, hasPhone, alarm, sms, now);
  }
};

static void aLiveCriticalSoundsAndQueuesItsSms() {
  Cabin c;
  CHECK(c.feed(line("m1", "medium", "low"), 0).vision == VisionChange::None);  // medium: nothing
  CHECK(!c.sms.pending());
  RecordDecision d;
  CabinSignals s = c.feed(line("c1", "critical", "medium"), 1000, false, &d);
  CHECK(d.action == RecordAction::Take);
  CHECK(s.vision == VisionChange::On);
  CHECK(c.alarm.buzzer());
  CHECK(s.sms.result == SmsQueued::Queued);
  int i = c.sms.due(1000);
  CHECK(i >= 0);
  CHECK(c.sms.slot(i).episode == "ep1");
  CHECK(c.sms.slot(i).alertType == "eyes_closed");
  CHECK(c.sms.slot(i).position.known && c.sms.slot(i).position.lat == 20.659712);  // the event's
}

static void aRowServedAgainChangesNothing() {
  Cabin c;
  CHECK(c.feed(line("c1", "critical", "medium"), 0).vision == VisionChange::On);
  // The lease lapsed on the Pi and it served the row again: TAKEN again (held once), no restart.
  RecordDecision d;
  CabinSignals again = c.feed(line("c1", "critical", "medium"), 125000, false, &d);
  CHECK(d.action == RecordAction::Take && d.take == TakeResult::AlreadyHeld);
  CHECK(again.vision == VisionChange::None);
  CHECK(again.sms.result == SmsQueued::Duplicate);
  // The driver OK ends it; the same critical served once more (still held) doesn't restart it.
  CabinSignals ok = c.feed(line("o1", "low", "critical", "ok"), 130000);
  CHECK(ok.vision == VisionChange::Off);
  CHECK(!c.alarm.buzzer());
  CHECK(c.feed(line("c1", "critical", "medium"), 131000).vision == VisionChange::None);
  CHECK(!c.alarm.buzzer());
}

static void aSecondCriticalOfTheEpisodeChangesNothing() {
  Cabin c;
  CHECK(c.feed(line("c1", "critical", "medium"), 0).vision == VisionChange::On);
  CHECK(c.feed(line("o1", "low", "critical", "ok"), 4000).vision == VisionChange::Off);
  // A later escalation of the same episode (e.g. a re-delivered one).
  CabinSignals s = c.feed(line("c2", "critical", "medium", "head_down"), 5000);
  CHECK(s.vision == VisionChange::None);
  CHECK(s.sms.result == SmsQueued::Duplicate);
  // A new episode sounds and texts again.
  s = c.feed(line("c3", "critical", "medium", "head_down", "ep2"), 60000);
  CHECK(s.vision == VisionChange::On);
  CHECK(s.sms.result == SmsQueued::Queued);
}

static void noFaceTextsButNeverSounds() {
  Cabin c;
  CabinSignals s = c.feed(line("n1", "critical", "none", "no_face"), 0);
  CHECK(s.vision == VisionChange::NoFace);
  CHECK(!c.alarm.buzzer());
  CHECK(s.sms.result == SmsQueued::Queued);
  CHECK(c.sms.slot(c.sms.due(0)).alertType == "no_face");
}

static void aDeadLetterNeverSoundsNorTexts() {
  Cabin c;
  std::string dlqLine = line("d1", "critical", "medium", "eyes_closed", "ep9",
                             R"(,"dlq":{"error":"no_network","body":null,"failed_at_ms":1,)"
                             R"("attempts":1,"queued_s":30})");
  RecordDecision d;
  CabinSignals s = c.feed(dlqLine, 0, /*dlq=*/true, &d);
  CHECK(d.action == RecordAction::Take);  // replayed to the backend, but history for the cabin
  CHECK(s.vision == VisionChange::None);
  CHECK(s.sms.result == SmsQueued::Ignored);
  CHECK(!c.alarm.buzzer() && !c.sms.pending());
}

static void aCriticalLeftOnThePiWaitsUntilItIsTaken() {
  Cabin c;
  for (int i = 0; i < SenderPolicy::kLiveSlots; i++) {  // every live slot busy
    std::string id = "m" + std::to_string(i);
    std::string ep = "epm" + std::to_string(i);
    c.feed(line(id.c_str(), "medium", "low", "eyes_closed", ep.c_str()), i);
  }
  RecordDecision d;
  CabinSignals s = c.feed(line("c1", "critical", "medium"), 100, false, &d);
  CHECK(d.action == RecordAction::Leave && d.take == TakeResult::Full);
  CHECK(s.vision == VisionChange::None);
  CHECK(s.sms.result == SmsQueued::Ignored);
  // A slot frees up; the Pi serves the row again and it is taken now.
  c.sender.beginAttempt(c.sender.plan(200, true), 200);
  c.sender.endAttempt(AttemptEnd::Delivered, "", 300);
  s = c.feed(line("c1", "critical", "medium"), 2000);
  CHECK(s.vision == VisionChange::On);
  CHECK(s.sms.result == SmsQueued::Queued);
}

static void noPhoneIsReportedOncePerEpisode() {
  Cabin c;
  c.hasPhone = false;
  CHECK(c.feed(line("c1", "critical", "medium"), 0).sms.result == SmsQueued::NoPhone);
  CHECK(c.feed(line("c1", "critical", "medium"), 2000).sms.result == SmsQueued::Duplicate);
  CHECK(!c.sms.pending());
  CHECK(c.alarm.buzzer());  // the buzzer doesn't need a phone
}

static void unusableAndNonEventRecordsChangeNothing() {
  Cabin c;
  CHECK(c.feed(R"({"id":"x1","kind":"route_status"})", 0).sms.result == SmsQueued::Ignored);
  CHECK(c.feed("not json", 0).vision == VisionChange::None);
  // A keepalive at critical (transition false) is not an event.
  std::string keepalive = line("k1", "critical", "critical");
  size_t at = keepalive.find("\"transition\":true");
  keepalive.replace(at, 17, "\"transition\":false");
  CHECK(c.feed(keepalive, 0).vision == VisionChange::None);
  CHECK(!c.alarm.buzzer() && !c.sms.pending());
}

int main() {
  RUN(aLiveCriticalSoundsAndQueuesItsSms);
  RUN(aRowServedAgainChangesNothing);
  RUN(aSecondCriticalOfTheEpisodeChangesNothing);
  RUN(noFaceTextsButNeverSounds);
  RUN(aDeadLetterNeverSoundsNorTexts);
  RUN(aCriticalLeftOnThePiWaitsUntilItIsTaken);
  RUN(noPhoneIsReportedOncePerEpisode);
  RUN(unusableAndNonEventRecordsChangeNothing);
  return report("cabin_signals_host_test");
}
