// The cabin hook (grip-alarm contracts C4, C5, changed 2026-10-31) end to end: real record lines
// through parsePiRecord() -> decidePulledRecord() -> cabinSignalsFor(), as the sketch's Bluetooth
// session runs them, plus the grip side as onGripChange() runs it (setGripAlarm + cabinAlarmSms).
// The double check: the buzzer and the one SMS need a live camera critical AND the hands off the
// wheel, whichever comes first; a re-pulled row or a DLQ replay changes nothing, a driver OK or
// the hands back stops the buzzer.
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

static EventPosition gripHere() {
  EventPosition p;
  p.known = true;
  p.lat = 21.0;
  p.lon = -104.0;
  return p;
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

  // As onGripChange() does on a grip episode's start / end.
  SmsQueueResult handsOff(uint32_t now) {
    alarm.setGripAlarm(true);
    return cabinAlarmSms(alarm, sms, gripHere(), hasPhone, now);
  }
  void handsBack() { alarm.setGripAlarm(false); }
};

static void handsOffFirstThenTheCameraSoundsAndTexts() {
  Cabin c;
  CHECK(c.handsOff(0).result == SmsQueued::Ignored);  // the wheel alone: nothing
  CHECK(!c.alarm.buzzer() && !c.sms.pending());
  CHECK(c.feed(line("m1", "medium", "low"), 500).vision == VisionChange::None);  // medium
  CHECK(!c.alarm.buzzer());
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

static void theCameraFirstThenHandsOffSoundsAndTexts() {
  Cabin c;
  CabinSignals s = c.feed(line("c1", "critical", "medium", "head_down"), 0);
  CHECK(s.vision == VisionChange::On);
  CHECK(s.sms.result == SmsQueued::Ignored);  // the camera alone: nothing
  CHECK(!c.alarm.buzzer() && !c.sms.pending());
  SmsQueueResult r = c.handsOff(3000);
  CHECK(r.result == SmsQueued::Queued);
  CHECK(c.alarm.buzzer());
  const SmsMessage& m = c.sms.slot(c.sms.due(3000));
  CHECK(m.episode == "ep1");               // the camera's episode
  CHECK(m.alertType == "head_down");       // the camera's reason
  CHECK(m.position.lat == 21.0);           // where the truck was when both came together
}

static void handsBackSilencesAndOneSmsPerCameraEpisode() {
  Cabin c;
  c.feed(line("c1", "critical", "medium"), 0);
  CHECK(c.handsOff(1000).result == SmsQueued::Queued);
  c.handsBack();
  CHECK(!c.alarm.buzzer());  // silent at once
  CHECK(c.handsOff(5000).result == SmsQueued::Duplicate);  // same episode: buzzer, no 2nd SMS
  CHECK(c.alarm.buzzer());
  // The driver OK: silent, the hands still off.
  CHECK(c.feed(line("o1", "low", "critical", "ok"), 8000).vision == VisionChange::Off);
  CHECK(!c.alarm.buzzer());
  // A new camera episode with the hands still off: sounds and texts again.
  CabinSignals s = c.feed(line("c2", "critical", "medium", "eyes_closed", "ep2"), 60000);
  CHECK(s.vision == VisionChange::On);
  CHECK(s.sms.result == SmsQueued::Queued);
  CHECK(c.alarm.buzzer());
}

static void aRowServedAgainChangesNothing() {
  Cabin c;
  c.handsOff(0);
  CHECK(c.feed(line("c1", "critical", "medium"), 0).vision == VisionChange::On);
  // The lease lapsed on the Pi and it served the row again: TAKEN again (held once), no restart.
  RecordDecision d;
  CabinSignals again = c.feed(line("c1", "critical", "medium"), 125000, false, &d);
  CHECK(d.action == RecordAction::Take && d.take == TakeResult::AlreadyHeld);
  CHECK(again.vision == VisionChange::None);
  CHECK(again.sms.result == SmsQueued::Ignored);
  // The driver OK ends it; the same critical served once more (still held) doesn't restart it.
  CabinSignals ok = c.feed(line("o1", "low", "critical", "ok"), 130000);
  CHECK(ok.vision == VisionChange::Off);
  CHECK(!c.alarm.buzzer());
  CHECK(c.feed(line("c1", "critical", "medium"), 131000).vision == VisionChange::None);
  CHECK(!c.alarm.buzzer());
}

static void aSecondCriticalOfTheEpisodeChangesNothing() {
  Cabin c;
  c.handsOff(0);
  CHECK(c.feed(line("c1", "critical", "medium"), 0).vision == VisionChange::On);
  CHECK(c.feed(line("o1", "low", "critical", "ok"), 4000).vision == VisionChange::Off);
  // A later escalation of the same episode (e.g. a re-delivered one).
  CabinSignals s = c.feed(line("c2", "critical", "medium", "head_down"), 5000);
  CHECK(s.vision == VisionChange::None);
  CHECK(s.sms.result == SmsQueued::Ignored);
  CHECK(!c.alarm.buzzer());
}

static void noFaceCountsLikeAnyOtherCritical() {
  Cabin c;
  c.handsOff(0);
  CabinSignals s = c.feed(line("n1", "critical", "none", "no_face"), 0);
  CHECK(s.vision == VisionChange::On);
  CHECK(c.alarm.buzzer());
  CHECK(s.sms.result == SmsQueued::Queued);
  CHECK(c.sms.slot(c.sms.due(0)).alertType == "no_face");
}

static void aDeadLetterNeverSoundsNorTexts() {
  Cabin c;
  c.handsOff(0);
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
  c.handsOff(0);
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
  CHECK(!c.alarm.buzzer());
  // A slot frees up; the Pi serves the row again and it is taken now.
  c.sender.beginAttempt(c.sender.plan(200, true), 200);
  c.sender.endAttempt(AttemptEnd::Delivered, "", 300);
  s = c.feed(line("c1", "critical", "medium"), 2000);
  CHECK(s.vision == VisionChange::On);
  CHECK(s.sms.result == SmsQueued::Queued);
  CHECK(c.alarm.buzzer());
}

static void noPhoneIsReportedOncePerEpisode() {
  Cabin c;
  c.hasPhone = false;
  c.handsOff(0);
  CHECK(c.feed(line("c1", "critical", "medium"), 0).sms.result == SmsQueued::NoPhone);
  c.handsBack();
  CHECK(c.handsOff(2000).result == SmsQueued::Duplicate);
  CHECK(!c.sms.pending());
  CHECK(c.alarm.buzzer());  // the buzzer doesn't need a phone
}

static void unusableAndNonEventRecordsChangeNothing() {
  Cabin c;
  c.handsOff(0);
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
  RUN(handsOffFirstThenTheCameraSoundsAndTexts);
  RUN(theCameraFirstThenHandsOffSoundsAndTexts);
  RUN(handsBackSilencesAndOneSmsPerCameraEpisode);
  RUN(aRowServedAgainChangesNothing);
  RUN(aSecondCriticalOfTheEpisodeChangesNothing);
  RUN(noFaceCountsLikeAnyOtherCritical);
  RUN(aDeadLetterNeverSoundsNorTexts);
  RUN(aCriticalLeftOnThePiWaitsUntilItIsTaken);
  RUN(noPhoneIsReportedOncePerEpisode);
  RUN(unusableAndNonEventRecordsChangeNothing);
  return report("cabin_signals_host_test");
}
