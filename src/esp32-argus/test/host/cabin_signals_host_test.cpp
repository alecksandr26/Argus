// The cabin hook (grip-alarm contracts C4, C5, changed 2026-11-03) end to end: real record lines
// through parsePiRecord() -> decidePulledRecord() -> cabinSignalsFor(), as the sketch's Bluetooth
// session runs them, plus the grip side as onGripChange() runs it (setGripAlarm +
// SmsPolicy::onGripCritical) and the grip task's AlarmPolicy::tick().
// - Every critical texts: a live, taken camera critical at once (hands on or off, the text says
//   which), a grip episode's start (its no_grip critical); a re-pulled row, a later critical of
//   the episode or a DLQ replay never does.
// - The buzzer: hands off 3 s (the grip episode) sounds, camera critical or not; at most 3 s
//   while the latest reading (keepalives included) says nobody is in view ("No Face" class or a
//   no_face critical).
// SOURCES: cabin_signals.cpp alarm_policy.cpp sms_policy.cpp pull_session.cpp sender_policy.cpp backend_messages.cpp pi_event_policy.cpp pi_record.cpp gps_track.cpp pi_protocol.cpp
// NEEDS: arduinojson
#include <ArduinoJson.h>

#include <string>

#include "cabin_signals.h"
#include "check.h"

static std::string line(const char* id, const char* severity, const char* previous,
                        const char* reason = "eyes_closed", const char* episode = "ep1",
                        const char* extra = "", bool transition = true) {
  return std::string(R"({"id":")") + id +
         R"(","kind":"drowsiness","level":2,"created_at_ms":1,"source_id":"c","age_s":0.4,)" +
         R"("payload":{"class_name":"Drowsy","probabilities":[0.1,0.9],"transition":)" +
         (transition ? "true" : "false") + R"(,"severity":")" + severity +
         R"(","previous_severity":")" + previous + R"(","episode":")" + episode +
         R"(","reason":")" + reason + R"(","model":"parts-rules-v1"})" + extra + "}";
}

// A keepalive: same severity, transition false (never an event).
static std::string keepalive(const char* id, const char* severity, const char* reason,
                             const char* episode = "ep1") {
  return line(id, severity, severity, reason, episode, "", false);
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
  int gripEpisodes = 0;

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
    std::string episode = "grip-0000abcd-" + std::to_string(++gripEpisodes);
    alarm.setGripAlarm(true, now);
    return sms.onGripCritical(episode, gripHere(), hasPhone, now);
  }
  void handsBack(uint32_t now) { alarm.setGripAlarm(false, now); }

  // As the grip task's applyBuzzer() does.
  bool buzzerAt(uint32_t now) {
    alarm.tick(now);
    return alarm.buzzer();
  }

  // The queued message for `episode`, or nullptr.
  const SmsMessage* message(const std::string& episode) const {
    for (int i = 0; i < SmsPolicy::kSlots; i++) {
      if (sms.slot(i).used && sms.slot(i).episode == episode) return &sms.slot(i);
    }
    return nullptr;
  }
};

static void aCameraCriticalTextsAtOnceWithTheHandsOn() {
  Cabin c;
  CHECK(c.feed(line("m1", "medium", "low"), 500).vision == VisionChange::None);  // medium
  CHECK(!c.sms.pending());
  RecordDecision d;
  CabinSignals s = c.feed(line("c1", "critical", "medium"), 1000, false, &d);
  CHECK(d.action == RecordAction::Take);
  CHECK(s.vision == VisionChange::On);
  CHECK(s.sms.result == SmsQueued::Queued);  // no hands-off needed any more
  const SmsMessage* m = c.message("ep1");
  CHECK(m != nullptr);
  CHECK(m->alertType == "eyes_closed");
  CHECK(!m->handsOff);  // the text won't say "sin manos"
  CHECK(m->position.known && m->position.lat == 20.659712);  // the event's position
  CHECK(!c.buzzerAt(1000));  // but the buzzer still needs the hands off
}

static void handsOffTextsThenACameraCriticalTextsAndSounds() {
  Cabin c;
  SmsQueueResult g = c.handsOff(0);
  CHECK(g.result == SmsQueued::Queued);  // the no_grip critical texts on its own
  const SmsMessage* gm = c.message("grip-0000abcd-1");
  CHECK(gm != nullptr && gm->alertType == "no_grip" && gm->handsOff && gm->position.lat == 21.0);
  CHECK(c.buzzerAt(100));  // hands off 3 s: sounds at the grip episode's start
  CHECK(c.alarm.cause() == BuzzerCause::GripOnly);
  CabinSignals s = c.feed(line("c1", "critical", "medium", "head_down"), 1000);
  CHECK(s.vision == VisionChange::On);
  CHECK(s.sms.result == SmsQueued::Queued);
  const SmsMessage* m = c.message("ep1");
  CHECK(m != nullptr && m->alertType == "head_down" && m->handsOff);
  CHECK(c.buzzerAt(1000));  // a camera critical + hands off: at once
  CHECK(c.alarm.cause() == BuzzerCause::CameraAndGrip);
}

static void handsOffAloneSoundsAtThreeSeconds() {
  Cabin c;
  c.feed(keepalive("k1", "none", "ok", ""), 9500);  // the camera sees a fine driver
  CHECK(!c.buzzerAt(9999));
  // The grip episode starts at 3 s without pressure (t = 10000 here): the buzzer with it.
  c.handsOff(10000);
  CHECK(c.buzzerAt(10000));
  CHECK(c.alarm.cause() == BuzzerCause::GripOnly);
  c.handsBack(13000);
  CHECK(!c.buzzerAt(13000));
}

// The driver steps out with the hands off: the Pi's first face-less record (class "No Face",
// severity none, ~0.6 s after the face is gone) starts the 3 s cap, well before the 5 s critical.
static void theFirstNoFaceRecordStartsTheCap() {
  Cabin c;
  c.feed(keepalive("k1", "none", "ok", ""), 0);
  c.handsOff(1000);
  CHECK(c.buzzerAt(1000));
  std::string noFace = std::string(R"({"id":"nf1","kind":"drowsiness","level":null,"created_at_ms":1,)") +
                       R"("source_id":"c","age_s":0.4,"payload":{"class_name":"No Face",)" +
                       R"("probabilities":null,"transition":true,"severity":"none",)" +
                       R"("previous_severity":"none"}})";
  CabinSignals s = c.feed(noFace, 2000);
  CHECK(s.vision == VisionChange::None && s.sms.result == SmsQueued::Ignored);
  CHECK(c.alarm.cameraNoFace());
  CHECK(c.buzzerAt(2000));  // the grip task ticks every 100 ms: the cap starts here
  CHECK(c.buzzerAt(4999));
  CHECK(!c.buzzerAt(5000));
  CHECK(c.alarm.cause() == BuzzerCause::NoFaceMuted);
}

static void oneSmsPerCameraEpisodeAndOnePerGripEpisode() {
  Cabin c;
  c.feed(line("c1", "critical", "medium"), 0);
  CHECK(c.handsOff(1000).result == SmsQueued::Queued);  // grip episode 1
  CHECK(c.buzzerAt(1000));
  c.handsBack(2000);
  CHECK(!c.buzzerAt(2000));  // silent at once
  CHECK(c.handsOff(5000).result == SmsQueued::Queued);  // grip episode 2: its own SMS
  CHECK(c.buzzerAt(5000));  // the camera episode still on: at once
  // The driver OK: the camera part ends; the hands are still off, so the hands-off rule goes on.
  CHECK(c.feed(line("o1", "low", "critical", "ok"), 6000).vision == VisionChange::Off);
  CHECK(c.buzzerAt(6000));
  CHECK(c.alarm.cause() == BuzzerCause::GripOnly);
  // A new camera episode: texts again.
  CabinSignals s = c.feed(line("c2", "critical", "medium", "eyes_closed", "ep2"), 60000);
  CHECK(s.vision == VisionChange::On);
  CHECK(s.sms.result == SmsQueued::Queued);
}

static void aRowServedAgainChangesNothing() {
  Cabin c;
  c.handsOff(0);
  CabinSignals first = c.feed(line("c1", "critical", "medium"), 0);
  CHECK(first.vision == VisionChange::On && first.sms.result == SmsQueued::Queued);
  // The lease lapsed on the Pi and it served the row again: TAKEN again (held once), no restart.
  RecordDecision d;
  CabinSignals again = c.feed(line("c1", "critical", "medium"), 125000, false, &d);
  CHECK(d.action == RecordAction::Take && d.take == TakeResult::AlreadyHeld);
  CHECK(again.vision == VisionChange::None);
  CHECK(again.sms.result == SmsQueued::Ignored);
  // The driver OK ends it; the same critical served once more (still held) doesn't restart it.
  CabinSignals ok = c.feed(line("o1", "low", "critical", "ok"), 130000);
  CHECK(ok.vision == VisionChange::Off);
  CHECK(c.feed(line("c1", "critical", "medium"), 131000).vision == VisionChange::None);
  CHECK(!c.alarm.visionAlarm());
}

static void aSecondCriticalOfTheEpisodeChangesNothing() {
  Cabin c;
  CHECK(c.feed(line("c1", "critical", "medium"), 0).vision == VisionChange::On);
  CHECK(c.feed(line("o1", "low", "critical", "ok"), 4000).vision == VisionChange::Off);
  // A later escalation of the same episode (e.g. a re-delivered one).
  CabinSignals s = c.feed(line("c2", "critical", "medium", "head_down"), 5000);
  CHECK(s.vision == VisionChange::None);
  CHECK(s.sms.result == SmsQueued::Ignored);
}

// The driver left the cab: the no_face critical texts, the buzzer sounds 3 s then stays quiet
// while the keepalives say no_face, and comes back the moment a keepalive shows a face again.
static void noFaceTextsAndSoundsThreeSeconds() {
  Cabin c;
  c.handsOff(0);
  CabinSignals s = c.feed(line("n1", "critical", "none", "no_face"), 500);
  CHECK(s.vision == VisionChange::On);
  CHECK(s.sms.result == SmsQueued::Queued);
  const SmsMessage* m = c.message("ep1");
  CHECK(m != nullptr && m->alertType == "no_face" && m->handsOff);
  CHECK(c.buzzerAt(500));
  c.feed(keepalive("k1", "critical", "no_face"), 2500);
  CHECK(c.buzzerAt(3499));
  CHECK(!c.buzzerAt(3500));
  CHECK(c.alarm.cause() == BuzzerCause::NoFaceMuted);
  for (uint32_t t = 4500; t <= 30500; t += 2000) {
    std::string id = "k" + std::to_string(t);
    c.feed(keepalive(id.c_str(), "critical", "no_face"), t);
    CHECK(!c.buzzerAt(t + 100));
  }
  // Back in the seat, asleep: the Pi sends no event (same severity), only the keepalive's reason.
  CabinSignals back = c.feed(keepalive("kb", "critical", "eyes_closed"), 32000);
  CHECK(back.vision == VisionChange::None && back.sms.result == SmsQueued::Ignored);
  CHECK(c.buzzerAt(32000));
  CHECK(c.alarm.cause() == BuzzerCause::CameraAndGrip);
}

static void aDeadLetterNeverSoundsNorTexts() {
  Cabin c;
  std::string dlqLine = line("d1", "critical", "medium", "no_face", "ep9",
                             R"(,"dlq":{"error":"no_network","body":null,"failed_at_ms":1,)"
                             R"("attempts":1,"queued_s":30})");
  RecordDecision d;
  CabinSignals s = c.feed(dlqLine, 0, /*dlq=*/true, &d);
  CHECK(d.action == RecordAction::Take);  // replayed to the backend, but history for the cabin
  CHECK(s.vision == VisionChange::None);
  CHECK(s.sms.result == SmsQueued::Ignored);
  CHECK(!c.sms.pending());
  CHECK(!c.alarm.cameraNoFace());  // nor the camera's latest reading
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
  CHECK(!c.alarm.visionAlarm());
  // A slot frees up; the Pi serves the row again and it is taken now.
  c.sender.beginAttempt(c.sender.plan(200, true), 200);
  c.sender.endAttempt(AttemptEnd::Delivered, "", 300);
  s = c.feed(line("c1", "critical", "medium"), 1000);
  CHECK(s.vision == VisionChange::On);
  CHECK(s.sms.result == SmsQueued::Queued);
  CHECK(c.buzzerAt(1000));
}

static void noPhoneIsReportedOncePerEpisode() {
  Cabin c;
  c.hasPhone = false;
  CHECK(c.handsOff(0).result == SmsQueued::NoPhone);
  CHECK(c.feed(line("c1", "critical", "medium"), 0).sms.result == SmsQueued::NoPhone);
  CHECK(!c.sms.pending());
  CHECK(c.buzzerAt(0));  // the buzzer doesn't need a phone
}

static void unusableAndNonEventRecordsChangeNothingButTheReading() {
  Cabin c;
  CHECK(c.feed(R"({"id":"x1","kind":"route_status"})", 0).sms.result == SmsQueued::Ignored);
  CHECK(c.feed("not json", 0).vision == VisionChange::None);
  // A keepalive at critical (transition false) is not an event...
  CHECK(c.feed(keepalive("k1", "critical", "eyes_closed"), 0).vision == VisionChange::None);
  CHECK(!c.sms.pending() && !c.alarm.visionAlarm());
  CHECK(!c.alarm.cameraNoFace());
  // ...but it is the camera's latest reading.
  c.feed(keepalive("k2", "critical", "no_face"), 2000);
  CHECK(c.alarm.cameraNoFace());
  c.feed(keepalive("k3", "none", "ok", ""), 4000);
  CHECK(!c.alarm.cameraNoFace());
}

int main() {
  RUN(aCameraCriticalTextsAtOnceWithTheHandsOn);
  RUN(handsOffTextsThenACameraCriticalTextsAndSounds);
  RUN(handsOffAloneSoundsAtThreeSeconds);
  RUN(theFirstNoFaceRecordStartsTheCap);
  RUN(oneSmsPerCameraEpisodeAndOnePerGripEpisode);
  RUN(aRowServedAgainChangesNothing);
  RUN(aSecondCriticalOfTheEpisodeChangesNothing);
  RUN(noFaceTextsAndSoundsThreeSeconds);
  RUN(aDeadLetterNeverSoundsNorTexts);
  RUN(aCriticalLeftOnThePiWaitsUntilItIsTaken);
  RUN(noPhoneIsReportedOncePerEpisode);
  RUN(unusableAndNonEventRecordsChangeNothingButTheReading);
  return report("cabin_signals_host_test");
}
