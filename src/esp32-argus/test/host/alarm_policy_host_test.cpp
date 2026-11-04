// When the cabin buzzer sounds (grip-alarm contracts C4, changed 2026-11-03): never with the hands
// on the wheel; hands off + a camera critical at once; hands off alone after GRIP_ONLY_BUZZER_MS (3 s);
// at most NO_FACE_BUZZER_MAX_MS while the camera's latest reading is a no_face critical. The vision
// alarm on for a live, taken critical (no_face included), off on the driver OK or the 5-minute
// cap, never from a medium or a DLQ replay, and never restarted by a re-pulled record or a later
// critical of the same episode.
// SOURCES: alarm_policy.cpp
#include <string>

#include "alarm_policy.h"
#include "check.h"

static VisionChange critical(AlarmPolicy& a, const char* episode, uint32_t now,
                             const char* reason = "eyes_closed", bool taken = true,
                             bool dlq = false) {
  return a.onPiRecord(PiEventKind::AlertCritical, dlq, taken, episode, reason, now);
}

static VisionChange driverOk(AlarmPolicy& a, const char* episode, uint32_t now) {
  return a.onPiRecord(PiEventKind::DriverOk, false, true, episode, "", now);
}

static void aLiveCriticalSoundsUntilTheDriverOk() {
  AlarmPolicy a;
  CHECK(!a.visionAlarm());
  CHECK(critical(a, "ep1", 1000) == VisionChange::On);
  CHECK(a.visionAlarm());
  CHECK(a.visionEpisode() == "ep1");
  CHECK(a.visionAlertType() == "eyes_closed");
  CHECK(driverOk(a, "ep1", 9000) == VisionChange::Off);
  CHECK(!a.visionAlarm());
}

static void everyReasonTurnsItOnNoFaceIncluded() {
  const char* reasons[] = {"eyes_closed", "yawn_eyes_closed", "head_down",
                           "head_down_eyes_closed", "no_face", "something_new"};
  int i = 0;
  for (const char* reason : reasons) {
    AlarmPolicy a;
    std::string episode = "ep" + std::to_string(i++);
    CHECK(critical(a, episode.c_str(), 0, reason) == VisionChange::On);
    CHECK(a.visionAlarm());
    CHECK(a.visionAlertType() == reason);
  }
}

static void mediumAndOtherRecordsNeverSound() {
  AlarmPolicy a;
  CHECK(a.onPiRecord(PiEventKind::AlertMedium, false, true, "ep1", "eyes_closed", 0) ==
        VisionChange::None);
  CHECK(a.onPiRecord(PiEventKind::None, false, false, "ep1", "eyes_closed", 0) ==
        VisionChange::None);
  CHECK(!a.visionAlarm());
}

static void aDlqReplayNeverSounds() {
  AlarmPolicy a;
  CHECK(critical(a, "ep1", 0, "eyes_closed", true, /*dlq=*/true) == VisionChange::None);
  CHECK(!a.visionAlarm());
  // ...and doesn't use the episode up either (it never sounded).
  CHECK(critical(a, "ep1", 10) == VisionChange::On);
}

static void aCriticalLeftOnThePiSoundsWhenItIsTaken() {
  AlarmPolicy a;
  CHECK(critical(a, "ep1", 0, "eyes_closed", /*taken=*/false) == VisionChange::None);
  CHECK(!a.visionAlarm());
  CHECK(critical(a, "ep1", 2000) == VisionChange::On);
}

static void aRepulledRecordDoesNotRestartAnEndedAlarm() {
  AlarmPolicy a;
  CHECK(critical(a, "ep1", 0) == VisionChange::On);
  CHECK(driverOk(a, "ep1", 4000) == VisionChange::Off);
  // The critical's lease lapsed on the Pi and it was served again: same record, same episode.
  CHECK(critical(a, "ep1", 125000) == VisionChange::None);
  CHECK(!a.visionAlarm());
}

static void aLaterCriticalOfTheSameEpisodeDoesNotRestartIt() {
  AlarmPolicy a;
  CHECK(critical(a, "ep1", 0) == VisionChange::On);
  CHECK(driverOk(a, "ep1", 4000) == VisionChange::Off);
  CHECK(critical(a, "ep1", 5000, "head_down") == VisionChange::None);
  CHECK(!a.visionAlarm());
  // While it sounds, a second critical of its episode doesn't restart the cap either.
  AlarmPolicy b;
  CHECK(critical(b, "ep2", 0) == VisionChange::On);
  CHECK(critical(b, "ep2", 200000, "head_down") == VisionChange::None);
  CHECK(!b.tick(299999));
  CHECK(b.tick(300000));  // 5 min from the first one
}

static void aNewEpisodeSoundsAgain() {
  AlarmPolicy a;
  CHECK(critical(a, "ep1", 0) == VisionChange::On);
  CHECK(driverOk(a, "ep1", 4000) == VisionChange::Off);
  CHECK(critical(a, "ep2", 60000) == VisionChange::On);
  CHECK(a.visionAlarm());
}

static void anOlderEpisodesOkDoesNotSilenceANewerAlarm() {
  AlarmPolicy a;
  CHECK(critical(a, "ep1", 0) == VisionChange::On);
  CHECK(critical(a, "ep2", 1000) == VisionChange::On);  // e.g. the Pi restarted mid-episode
  CHECK(a.visionEpisode() == "ep2");
  CHECK(driverOk(a, "ep1", 2000) == VisionChange::None);
  CHECK(a.visionAlarm());
  CHECK(driverOk(a, "ep2", 3000) == VisionChange::Off);
  CHECK(!a.visionAlarm());
  // An OK with no episode can't be told apart: it stops the alarm.
  CHECK(critical(a, "ep3", 4000) == VisionChange::On);
  CHECK(driverOk(a, "", 5000) == VisionChange::Off);
  // An OK with nothing sounding changes nothing.
  CHECK(driverOk(a, "ep3", 6000) == VisionChange::None);
}

static void theCapEndsAVisionAlarmTheOkNeverEnded() {
  AlarmPolicy a;
  CHECK(critical(a, "ep1", 1000) == VisionChange::On);
  CHECK(!a.tick(300999));
  CHECK(a.visionAlarm());
  CHECK(a.tick(301000));
  CHECK(!a.visionAlarm());
  CHECK(!a.tick(400000));  // once
  CHECK(critical(a, "ep1", 400000) == VisionChange::None);  // nor restarted by its episode
  // Across a millis() wrap.
  AlarmPolicy w(1000);
  CHECK(critical(w, "ep1", 0xFFFFFE00u) == VisionChange::On);
  CHECK(!w.tick(0x000001E7u));  // 999 ms
  CHECK(w.tick(0x000001E8u));   // 1000 ms
}

// The grip task reads millis() before it takes the lock; the loop task may set a vision alarm
// with a later millis() in between. That stale, slightly earlier clock must not look like a cap
// that ran out (unsigned now - since would be ~49 days) and silence the alarm at once.
static void aClockReadJustBeforeTheCriticalDoesNotTripTheCap() {
  AlarmPolicy a;
  CHECK(critical(a, "ep1", 10000) == VisionChange::On);
  CHECK(!a.tick(9999));
  CHECK(!a.tick(9000));
  CHECK(a.visionAlarm() && a.visionAlarm());
  CHECK(!a.tick(309999));
  CHECK(a.tick(310000));
  // The same just across a millis() wrap.
  AlarmPolicy w;
  CHECK(critical(w, "ep1", 2) == VisionChange::On);
  CHECK(!w.tick(0xFFFFFFFEu));  // 4 ms before it
  CHECK(w.visionAlarm());
}

// The camera's reading as a keepalive carries it: the class follows the severity ("Drowsy" for
// medium/critical, "Not Drowsy" for none/low), as the Pi's records do.
static void reading(AlarmPolicy& a, const char* severity, const char* reason, uint32_t now,
                    bool dlq = false) {
  std::string sev = severity;
  const char* cls = (sev == "critical" || sev == "medium") ? "Drowsy" : "Not Drowsy";
  a.onCameraReading(dlq, cls, severity, reason, now);
}

// The Pi's first face-less record: class "No Face", severity none, no reason.
static void noFaceClass(AlarmPolicy& a, uint32_t now) { a.onCameraReading(false, "No Face", "none", "", now); }

// These tests pin the delay mechanics with the pre-2026-11-03 grip-only delay (2 s into the grip
// episode); the shipped default (0: the buzzer at the grip episode's start) is defaultsSoundAtTheGripEpisodeStart.
static AlarmTiming delayed() {
  AlarmTiming t;
  t.gripOnlyAfterMs = 2000;
  return t;
}

// Hands off + a camera critical: sounds at once (no grip-only delay), whichever came first, and
// stops at once with the hands back or the driver OK.
static void handsOffAndACameraCriticalSoundAtOnce() {
  AlarmPolicy a(delayed());
  a.tick(0);
  CHECK(!a.buzzer());
  a.setGripAlarm(true, 0);  // hands off alone, under the grip-only delay: silent
  a.tick(100);
  CHECK(a.gripAlarm() && !a.visionAlarm() && !a.buzzer());
  CHECK(critical(a, "ep1", 200) == VisionChange::On);  // + the camera: sounds now
  a.tick(200);
  CHECK(a.buzzer() && a.cause() == BuzzerCause::CameraAndGrip);
  a.setGripAlarm(false, 300);  // the hands are back: silent at once, the vision alarm still on
  a.tick(300);
  CHECK(a.visionAlarm() && !a.buzzer() && a.cause() == BuzzerCause::None);
  a.setGripAlarm(true, 400);  // hands off again within the same camera episode: sounds again
  a.tick(400);
  CHECK(a.buzzer());
  CHECK(driverOk(a, "ep1", 1000) == VisionChange::Off);  // the driver OK: the camera part ends
  a.tick(1000);
  CHECK(a.gripAlarm() && !a.buzzer());  // grip episode 600 ms old: under the grip-only delay

  // Camera first, then the hands.
  AlarmPolicy b(delayed());
  CHECK(critical(b, "ep1", 0) == VisionChange::On);
  b.tick(0);
  CHECK(!b.buzzer());  // the camera alone: silent
  b.setGripAlarm(true, 1000);
  b.tick(1000);
  CHECK(b.buzzer() && b.cause() == BuzzerCause::CameraAndGrip);
  // The 5 min cap ends the vision alarm; the hands are still off, long past the grip-only delay.
  CHECK(b.tick(300000));
  CHECK(!b.visionAlarm() && b.buzzer() && b.cause() == BuzzerCause::GripOnly);
}

// Hands off with the camera OK: 5 s from the last pressure, i.e. 2 s into the grip episode.
static void handsOffAloneSoundsAfterTheGripOnlyDelay() {
  AlarmPolicy a(delayed());
  a.setGripAlarm(true, 10000);  // the grip episode starts (3 s without pressure)
  a.tick(11999);
  CHECK(!a.buzzer() && a.cause() == BuzzerCause::None);
  a.tick(12000);
  CHECK(a.buzzer() && a.cause() == BuzzerCause::GripOnly);
  reading(a, "none", "ok", 12100);  // the camera sees a fine driver: still sounds
  a.tick(12200);
  CHECK(a.buzzer());
  a.setGripAlarm(false, 30100);  // pressure back
  a.tick(30100);
  CHECK(!a.buzzer());
  // A new grip episode starts its own delay.
  a.setGripAlarm(true, 40000);
  a.tick(41999);
  CHECK(!a.buzzer());
  a.tick(42000);
  CHECK(a.buzzer());
  // setGripAlarm(true) again inside the episode doesn't restart the delay.
  AlarmPolicy b(delayed());
  b.setGripAlarm(true, 0);
  b.setGripAlarm(true, 1500);
  b.tick(2000);
  CHECK(b.buzzer());
  // Across a millis() wrap, and a clock read just before the start (another task's) is "not yet".
  AlarmPolicy w(delayed());
  w.setGripAlarm(true, 0xFFFFFC00u);
  w.tick(0xFFFFFB00u);
  CHECK(!w.buzzer());
  w.tick(0x000003CFu);  // 1999 ms
  CHECK(!w.buzzer());
  w.tick(0x000003D0u);  // 2000 ms
  CHECK(w.buzzer());
}

// The no-face cap: nobody in view -> 3 s of buzzer at most, then quiet, whatever the path.
static void noFaceSoundsThreeSecondsThenQuiet() {
  // The camera's no_face critical with the hands off (the driver left the cab).
  AlarmPolicy a(delayed());
  a.setGripAlarm(true, 0);
  reading(a, "critical", "no_face", 500);
  CHECK(critical(a, "ep1", 500, "no_face") == VisionChange::On);
  a.tick(500);
  CHECK(a.buzzer() && a.cause() == BuzzerCause::CameraAndGrip);
  a.tick(3499);
  CHECK(a.buzzer());
  a.tick(3500);
  CHECK(!a.buzzer() && a.cause() == BuzzerCause::NoFaceMuted);
  // Keepalives keep saying no_face: still quiet, past the grip-only delay too.
  for (uint32_t t = 4000; t <= 60000; t += 2000) {
    reading(a, "critical", "no_face", t);
    a.tick(t + 100);
    CHECK(!a.buzzer());
  }
  CHECK(a.visionAlarm());  // the vision alarm itself is untouched (the cap/OK end it)
}

static void noFaceAlsoCapsTheGripOnlyBuzzer() {
  // The grip-only buzzer is already sounding when the no_face critical arrives: 3 s from it.
  AlarmPolicy a(delayed());
  a.setGripAlarm(true, 0);
  a.tick(2000);
  CHECK(a.buzzer() && a.cause() == BuzzerCause::GripOnly);
  reading(a, "critical", "no_face", 5000);
  a.tick(5000);
  a.tick(7999);
  CHECK(a.buzzer());
  a.tick(8000);
  CHECK(!a.buzzer() && a.cause() == BuzzerCause::NoFaceMuted);
  // no_face first, hands off later: 3 s from when the buzzer would start.
  AlarmPolicy b(delayed());
  reading(b, "critical", "no_face", 0);
  b.setGripAlarm(true, 1000);
  reading(b, "critical", "no_face", 2000);
  b.tick(2999);
  CHECK(!b.buzzer() && b.cause() == BuzzerCause::None);  // grip-only delay not over yet
  b.tick(3000);
  CHECK(b.buzzer());
  reading(b, "critical", "no_face", 4000);
  b.tick(5999);
  CHECK(b.buzzer());
  b.tick(6000);
  CHECK(!b.buzzer());
}

// The face back -- even drowsy, with no new event from the Pi -- re-arms everything at once.
static void aReadingWithAFaceEndsTheCap() {
  AlarmPolicy a(delayed());
  a.setGripAlarm(true, 0);
  reading(a, "critical", "no_face", 0);
  CHECK(critical(a, "ep1", 0, "no_face") == VisionChange::On);
  a.tick(0);
  a.tick(3000);
  CHECK(!a.buzzer());
  // The driver is back asleep: same episode, same severity, only the keepalive's reason changes.
  reading(a, "critical", "eyes_closed", 8000);
  a.tick(8000);
  CHECK(a.buzzer() && a.cause() == BuzzerCause::CameraAndGrip);
  // Gone again: a fresh 3 s.
  reading(a, "critical", "no_face", 10000);
  a.tick(10000);
  CHECK(a.buzzer());
  reading(a, "critical", "no_face", 12000);
  a.tick(12999);
  CHECK(a.buzzer());
  a.tick(13000);
  CHECK(!a.buzzer());
  // The driver back and fine (severity down): the grip-only rule applies again.
  reading(a, "none", "ok", 14000);
  CHECK(driverOk(a, "ep1", 14000) == VisionChange::Off);
  a.tick(14000);
  CHECK(a.buzzer() && a.cause() == BuzzerCause::GripOnly);
}

// A no_face reading older than PI_STALE_MS (no link, a dead Pi) never mutes the wheel rule; a
// DLQ replay is history and doesn't count as a reading.
static void aStaleOrReplayedNoFaceDoesNotMute() {
  CHECK(PI_STALE_MS == 10000UL);
  AlarmPolicy a(delayed());
  a.setGripAlarm(true, 0);
  reading(a, "critical", "no_face", 0);
  a.tick(2000);
  a.tick(5000);
  CHECK(!a.buzzer());  // muted
  a.tick(9999);
  CHECK(!a.buzzer());
  a.tick(10000);  // nothing from the Pi for 10 s: the no_face reading is stale
  CHECK(a.buzzer() && a.cause() == BuzzerCause::GripOnly);

  AlarmPolicy b(delayed());
  b.setGripAlarm(true, 0);
  reading(b, "critical", "no_face", 0, /*dlq=*/true);
  CHECK(!b.cameraNoFace());
  b.tick(2000);
  b.tick(8000);
  CHECK(b.buzzer());
  // A face-in-view reading at severity none is not "nobody in view"; neither is a medium with a
  // stray no_face reason (only a critical one counts) -- but the "No Face" class is.
  reading(b, "none", "", 8000);
  CHECK(!b.cameraNoFace());
  reading(b, "medium", "no_face", 8100);
  CHECK(!b.cameraNoFace());
  noFaceClass(b, 8200);
  CHECK(b.cameraNoFace());
}

// The shipped defaults (2026-11-03): the hands-off buzzer at the grip episode's start (3 s without
// pressure), and "nobody in view" from the first "No Face" class reading, not only the 5 s
// no_face critical.
static void defaultsSoundAtTheGripEpisodeStart() {
  CHECK(GRIP_ONLY_BUZZER_MS == 3000UL && GRIP_ALERT_MS == 3000UL);
  AlarmPolicy a;
  reading(a, "none", "ok", 0);  // a face in view, fine
  a.setGripAlarm(true, 1000);  // 3 s without pressure
  a.tick(1000);
  CHECK(a.buzzer() && a.cause() == BuzzerCause::GripOnly);
  a.setGripAlarm(false, 1500);
  a.tick(1500);
  CHECK(!a.buzzer());
}

static void theNoFaceClassMutesAfterThreeSeconds() {
  AlarmPolicy a;
  reading(a, "none", "ok", 0);
  a.setGripAlarm(true, 0);
  a.tick(0);
  CHECK(a.buzzer());
  noFaceClass(a, 1000);  // the driver left: the Pi's first face-less record (severity none)
  a.tick(1000);
  CHECK(a.buzzer());
  noFaceClass(a, 3000);  // its keepalive
  a.tick(3999);
  CHECK(a.buzzer());
  a.tick(4000);  // 3 s after the no-face reading
  CHECK(!a.buzzer() && a.cause() == BuzzerCause::NoFaceMuted);
  // The 5 s no_face critical (class "Drowsy") keeps it quiet.
  CHECK(critical(a, "ep1", 5000, "no_face") == VisionChange::On);
  a.onCameraReading(false, "Drowsy", "critical", "no_face", 5000);
  a.tick(5000);
  CHECK(!a.buzzer());
  // A face again (fine): the hands are still off, so it sounds again at once.
  reading(a, "none", "ok", 9000);
  a.tick(9000);
  CHECK(a.buzzer());
}

static void theRingForgetsOnlyLongAgoEpisodes() {
  AlarmPolicy a;
  for (int i = 0; i < AlarmPolicy::kRemembered; i++) {
    std::string episode = "ep" + std::to_string(i);
    CHECK(critical(a, episode.c_str(), i) == VisionChange::On);
  }
  CHECK(critical(a, "ep0", 100) == VisionChange::None);  // still remembered
  CHECK(critical(a, "ep-new", 200) == VisionChange::On);  // pushes ep0 out
  CHECK(critical(a, "ep1", 300) == VisionChange::None);
  CHECK(critical(a, "ep0", 400) == VisionChange::On);  // only 9 episodes later
}

int main() {
  RUN(aLiveCriticalSoundsUntilTheDriverOk);
  RUN(everyReasonTurnsItOnNoFaceIncluded);
  RUN(mediumAndOtherRecordsNeverSound);
  RUN(aDlqReplayNeverSounds);
  RUN(aCriticalLeftOnThePiSoundsWhenItIsTaken);
  RUN(aRepulledRecordDoesNotRestartAnEndedAlarm);
  RUN(aLaterCriticalOfTheSameEpisodeDoesNotRestartIt);
  RUN(aNewEpisodeSoundsAgain);
  RUN(anOlderEpisodesOkDoesNotSilenceANewerAlarm);
  RUN(theCapEndsAVisionAlarmTheOkNeverEnded);
  RUN(aClockReadJustBeforeTheCriticalDoesNotTripTheCap);
  RUN(handsOffAndACameraCriticalSoundAtOnce);
  RUN(handsOffAloneSoundsAfterTheGripOnlyDelay);
  RUN(noFaceSoundsThreeSecondsThenQuiet);
  RUN(noFaceAlsoCapsTheGripOnlyBuzzer);
  RUN(aReadingWithAFaceEndsTheCap);
  RUN(aStaleOrReplayedNoFaceDoesNotMute);
  RUN(defaultsSoundAtTheGripEpisodeStart);
  RUN(theNoFaceClassMutesAfterThreeSeconds);
  RUN(theRingForgetsOnlyLongAgoEpisodes);
  return report("alarm_policy_host_test");
}
