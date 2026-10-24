// When the cabin buzzer sounds (grip-alarm contracts C4): grip alarm || vision alarm; the vision
// alarm on for a live, taken critical that isn't no_face, off on the driver OK or the 5-minute cap,
// never from a medium or a DLQ replay, and never restarted by a re-pulled record or a later
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
  CHECK(!a.buzzer());
  CHECK(critical(a, "ep1", 1000) == VisionChange::On);
  CHECK(a.visionAlarm() && a.buzzer());
  CHECK(a.visionEpisode() == "ep1");
  CHECK(driverOk(a, "ep1", 9000) == VisionChange::Off);
  CHECK(!a.buzzer());
}

static void everyNonNoFaceReasonSounds() {
  const char* reasons[] = {"eyes_closed", "yawn_eyes_closed", "head_down",
                           "head_down_eyes_closed", "something_new"};
  int i = 0;
  for (const char* reason : reasons) {
    AlarmPolicy a;
    std::string episode = "ep" + std::to_string(i++);
    CHECK(critical(a, episode.c_str(), 0, reason) == VisionChange::On);
    CHECK(a.buzzer());
  }
}

static void noFaceNeverSounds() {
  AlarmPolicy a;
  CHECK(critical(a, "ep1", 0, "no_face") == VisionChange::NoFace);
  CHECK(!a.buzzer());
  // The same episode's later critical for another reason does sound: no_face didn't use it up.
  CHECK(critical(a, "ep1", 5000, "eyes_closed") == VisionChange::On);
  CHECK(a.buzzer());
}

static void mediumAndOtherRecordsNeverSound() {
  AlarmPolicy a;
  CHECK(a.onPiRecord(PiEventKind::AlertMedium, false, true, "ep1", "eyes_closed", 0) ==
        VisionChange::None);
  CHECK(a.onPiRecord(PiEventKind::None, false, false, "ep1", "eyes_closed", 0) ==
        VisionChange::None);
  CHECK(!a.buzzer());
}

static void aDlqReplayNeverSounds() {
  AlarmPolicy a;
  CHECK(critical(a, "ep1", 0, "eyes_closed", true, /*dlq=*/true) == VisionChange::None);
  CHECK(!a.buzzer());
  // ...and doesn't use the episode up either (it never sounded).
  CHECK(critical(a, "ep1", 10) == VisionChange::On);
}

static void aCriticalLeftOnThePiSoundsWhenItIsTaken() {
  AlarmPolicy a;
  CHECK(critical(a, "ep1", 0, "eyes_closed", /*taken=*/false) == VisionChange::None);
  CHECK(!a.buzzer());
  CHECK(critical(a, "ep1", 2000) == VisionChange::On);
}

static void aRepulledRecordDoesNotRestartAnEndedAlarm() {
  AlarmPolicy a;
  CHECK(critical(a, "ep1", 0) == VisionChange::On);
  CHECK(driverOk(a, "ep1", 4000) == VisionChange::Off);
  // The critical's lease lapsed on the Pi and it was served again: same record, same episode.
  CHECK(critical(a, "ep1", 125000) == VisionChange::None);
  CHECK(!a.buzzer());
}

static void aLaterCriticalOfTheSameEpisodeDoesNotRestartIt() {
  AlarmPolicy a;
  CHECK(critical(a, "ep1", 0) == VisionChange::On);
  CHECK(driverOk(a, "ep1", 4000) == VisionChange::Off);
  CHECK(critical(a, "ep1", 5000, "head_down") == VisionChange::None);
  CHECK(!a.buzzer());
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
  CHECK(a.buzzer());
}

static void anOlderEpisodesOkDoesNotSilenceANewerAlarm() {
  AlarmPolicy a;
  CHECK(critical(a, "ep1", 0) == VisionChange::On);
  CHECK(critical(a, "ep2", 1000) == VisionChange::On);  // e.g. the Pi restarted mid-episode
  CHECK(a.visionEpisode() == "ep2");
  CHECK(driverOk(a, "ep1", 2000) == VisionChange::None);
  CHECK(a.buzzer());
  CHECK(driverOk(a, "ep2", 3000) == VisionChange::Off);
  CHECK(!a.buzzer());
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
  CHECK(a.buzzer());
  CHECK(a.tick(301000));
  CHECK(!a.buzzer());
  CHECK(!a.tick(400000));  // once
  CHECK(critical(a, "ep1", 400000) == VisionChange::None);  // nor restarted by its episode
  // Across a millis() wrap.
  AlarmPolicy w(1000);
  CHECK(critical(w, "ep1", 0xFFFFFE00u) == VisionChange::On);
  CHECK(!w.tick(0x000001E7u));  // 999 ms
  CHECK(w.tick(0x000001E8u));   // 1000 ms
}

static void gripAndVisionAreOred() {
  AlarmPolicy a;
  a.setGripAlarm(true);
  CHECK(a.buzzer() && a.gripAlarm() && !a.visionAlarm());
  CHECK(critical(a, "ep1", 0) == VisionChange::On);
  a.setGripAlarm(false);  // the hands are back: the vision alarm still sounds
  CHECK(a.buzzer());
  CHECK(driverOk(a, "ep1", 1000) == VisionChange::Off);
  CHECK(!a.buzzer());
  a.setGripAlarm(true);
  CHECK(driverOk(a, "ep1", 2000) == VisionChange::None);  // the OK doesn't touch the grip alarm
  CHECK(a.buzzer());
  CHECK(!a.tick(400000));  // nor the cap
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
  RUN(everyNonNoFaceReasonSounds);
  RUN(noFaceNeverSounds);
  RUN(mediumAndOtherRecordsNeverSound);
  RUN(aDlqReplayNeverSounds);
  RUN(aCriticalLeftOnThePiSoundsWhenItIsTaken);
  RUN(aRepulledRecordDoesNotRestartAnEndedAlarm);
  RUN(aLaterCriticalOfTheSameEpisodeDoesNotRestartIt);
  RUN(aNewEpisodeSoundsAgain);
  RUN(anOlderEpisodesOkDoesNotSilenceANewerAlarm);
  RUN(theCapEndsAVisionAlarmTheOkNeverEnded);
  RUN(gripAndVisionAreOred);
  RUN(theRingForgetsOnlyLongAgoEpisodes);
  return report("alarm_policy_host_test");
}
