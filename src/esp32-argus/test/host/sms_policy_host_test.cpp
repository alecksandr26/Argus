// The critical-alert SMS (grip-alarm contracts C5, changed 2026-11-03: every critical texts): one
// per camera critical episode (onCameraCritical) and one per grip episode (onGripCritical), each
// deduped by its own ring, the queue of 4, retries, the pending timeout, and the exact text (the
// "sin manos" part only with the hands off). When a record is a new critical is
// cabin_signals_host_test's job.
// SOURCES: sms_policy.cpp
#include <string>

#include "check.h"
#include "sms_policy.h"

static EventPosition at(double lat, double lon) {
  EventPosition p;
  p.known = true;
  p.lat = lat;
  p.lon = lon;
  return p;
}

static const EventPosition kHere = at(20.659712, -103.349612);

static SmsQueueResult both(SmsPolicy& s, const char* episode, uint32_t now,
                           const char* reason = "eyes_closed", bool hasPhone = true) {
  return s.onCameraCritical(episode, reason, /*handsOff=*/true, kHere, hasPhone, now);
}

static int queued(const SmsPolicy& s) {
  int n = 0;
  for (int i = 0; i < SmsPolicy::kSlots; i++) n += s.slot(i).used ? 1 : 0;
  return n;
}

// --- Which moments get an SMS ---

static void aCameraCriticalQueuesOncePerEpisode() {
  SmsPolicy s;
  CHECK(both(s, "ep1", 0).result == SmsQueued::Queued);
  CHECK(s.pending());
  CHECK(s.slot(0).episode == "ep1");
  CHECK(s.slot(0).alertType == "eyes_closed");
  CHECK(s.slot(0).position.known && s.slot(0).position.lat == 20.659712);
  // The driver let go of the wheel again in the same episode, or a later critical of it: nothing.
  CHECK(both(s, "ep1", 5000).result == SmsQueued::Duplicate);
  CHECK(both(s, "ep1", 6000, "head_down").result == SmsQueued::Duplicate);
  CHECK(queued(s) == 1);
  // Even after its SMS went: the ring remembers the episode, not the queue.
  CHECK(s.attemptEnded("ep1", true, 7000) == SmsAttemptEnd::Sent);
  CHECK(!s.pending());
  CHECK(both(s, "ep1", 125000).result == SmsQueued::Duplicate);
  CHECK(both(s, "ep2", 130000).result == SmsQueued::Queued);
}

static void noFaceIsQueuedLikeAnyOtherCritical() {
  SmsPolicy s;
  CHECK(both(s, "ep1", 0, "no_face").result == SmsQueued::Queued);
  CHECK(s.slot(0).alertType == "no_face");
}

static void withNoPhoneAnEpisodeIsSkippedAndReportedOnce() {
  SmsPolicy s;
  CHECK(both(s, "ep1", 0, "eyes_closed", /*hasPhone=*/false).result == SmsQueued::NoPhone);
  CHECK(both(s, "ep1", 10, "eyes_closed", false).result == SmsQueued::Duplicate);
  CHECK(!s.pending());
}

static void theRingRemembersTheLastEightEpisodes() {
  SmsPolicy s;
  for (int i = 0; i < SmsPolicy::kRemembered; i++) {
    std::string ep = "ep" + std::to_string(i);
    CHECK(both(s, ep.c_str(), i, "eyes_closed", false).result == SmsQueued::NoPhone);
  }
  CHECK(both(s, "ep0", 100, "eyes_closed", false).result == SmsQueued::Duplicate);
  CHECK(both(s, "ep8", 200, "eyes_closed", false).result == SmsQueued::NoPhone);
  CHECK(both(s, "ep0", 300, "eyes_closed", false).result == SmsQueued::NoPhone);  // forgotten
}

// --- The queue ---

static void aFullQueueDropsTheOldestWaitingMessage() {
  SmsPolicy s;
  for (int i = 0; i < SmsPolicy::kSlots; i++) {
    std::string ep = "ep" + std::to_string(i);
    CHECK(both(s, ep.c_str(), 1000 * i).result == SmsQueued::Queued);
  }
  SmsQueueResult r = both(s, "ep9", 9000);
  CHECK(r.result == SmsQueued::Queued);
  CHECK(r.evicted && r.evictedEpisode == "ep0");
  CHECK(queued(s) == SmsPolicy::kSlots);
  CHECK(s.slot(s.due(9000)).episode == "ep1");  // the oldest left goes first
  // A message dropped while it was being sent: its outcome finds nothing.
  CHECK(s.attemptEnded("ep0", true, 9100) == SmsAttemptEnd::Gone);
}

static void oldestDueFirstAndRetriesTenSecondsApart() {
  SmsPolicy s;
  CHECK(s.due(0) == -1);
  CHECK(both(s, "ep1", 1000).result == SmsQueued::Queued);
  CHECK(both(s, "ep2", 2000).result == SmsQueued::Queued);
  int i = s.due(2000);
  CHECK(i >= 0 && s.slot(i).episode == "ep1");
  CHECK(s.attemptEnded("ep1", false, 3000) == SmsAttemptEnd::Retry);
  CHECK(s.slot(s.due(3000)).episode == "ep2");  // ep1 waits its gap; ep2 goes meanwhile
  CHECK(s.attemptEnded("ep2", true, 4000) == SmsAttemptEnd::Sent);
  CHECK(s.due(12999) == -1);
  CHECK(s.slot(s.due(13000)).episode == "ep1");
  CHECK(s.attemptEnded("ep1", false, 14000) == SmsAttemptEnd::Retry);
  CHECK(s.attemptEnded("ep1", false, 25000) == SmsAttemptEnd::GaveUp);  // the 3rd failure
  CHECK(!s.pending());
}

static void aMessageWaitingFiveMinutesIsDropped() {
  SmsPolicy s;
  CHECK(both(s, "ep1", 1000).result == SmsQueued::Queued);  // e.g. no GSM registration
  CHECK(both(s, "ep2", 61000).result == SmsQueued::Queued);
  SmsMessage dropped;
  CHECK(!s.expireOne(300999, dropped));
  CHECK(s.expireOne(301000, dropped));
  CHECK(dropped.episode == "ep1" && dropped.attempts == 0);
  CHECK(!s.expireOne(301000, dropped));
  CHECK(s.pending());
  CHECK(s.expireOne(361000, dropped) && dropped.episode == "ep2");
  CHECK(!s.pending());
}

static void dropRemovesAMessageWithoutAnAttempt() {
  SmsPolicy s;
  CHECK(both(s, "ep1", 0).result == SmsQueued::Queued);
  CHECK(s.drop("ep1"));
  CHECK(!s.drop("ep1"));
  CHECK(!s.pending());
  CHECK(both(s, "ep1", 10).result == SmsQueued::Duplicate);  // still its one chance
}

static void survivesMillisWrap() {
  SmsPolicy s;
  const uint32_t t0 = 0xFFFFFF00u;
  CHECK(both(s, "ep1", t0).result == SmsQueued::Queued);
  CHECK(s.attemptEnded("ep1", false, t0 + 100) == SmsAttemptEnd::Retry);
  CHECK(s.due(t0 + 10099) == -1);  // across the wrap
  CHECK(s.due(t0 + 10100) >= 0);
  SmsMessage dropped;
  CHECK(!s.expireOne(t0 + 299999, dropped));
  CHECK(s.expireOne(t0 + 300000, dropped));
}

// --- The text: exact strings ---

static void theTextWithAPosition() {
  CHECK(composeSmsText("eyes_closed", true, "JAL-123-A", kHere) ==
        "ARGUS ALERTA CRITICA: ojos cerrados, sin manos en el volante. Camion JAL-123-A. "
        "https://maps.google.com/?q=20.659712,-103.349612");
  CHECK(composeSmsText("head_down", true, "64b0a0000000000000000001", at(-33.5, 151.25)) ==
        "ARGUS ALERTA CRITICA: cabeza abajo, sin manos en el volante. "
        "Camion 64b0a0000000000000000001. https://maps.google.com/?q=-33.500000,151.250000");
}

static void theTextWithoutAPosition() {
  CHECK(composeSmsText("no_face", true, "JAL-123-A", EventPosition()) ==
        "ARGUS ALERTA CRITICA: conductor no visible, sin manos en el volante. Camion JAL-123-A. "
        "Ubicacion desconocida.");
}

static void everyMotive() {
  struct Case {
    const char* alertType;
    const char* motivo;
  } cases[] = {
      {"eyes_closed", "ojos cerrados"},
      {"yawn_eyes_closed", "ojos cerrados tras bostezo"},
      {"head_down", "cabeza abajo"},
      {"head_down_eyes_closed", "cabeza abajo y ojos cerrados"},
      {"no_face", "conductor no visible"},
  };
  for (const Case& k : cases) {
    CHECK(std::string(smsMotivo(k.alertType)) == k.motivo);
    CHECK(composeSmsText(k.alertType, true, "X1", EventPosition()) ==
          std::string("ARGUS ALERTA CRITICA: ") + k.motivo +
              ", sin manos en el volante. Camion X1. Ubicacion desconocida.");
  }
  CHECK(smsMotivo("mouth_open") == nullptr);
  CHECK(smsMotivo("no_grip") == nullptr);  // the grip text has no motive (theGripText)
  // Anything else: the raw reason, made GSM-safe ('_' isn't the same character in GSM-7).
  CHECK(composeSmsText("mouth_open", true, "X1", EventPosition()) ==
        "ARGUS ALERTA CRITICA: mouth open, sin manos en el volante. Camion X1. "
        "Ubicacion desconocida.");
  CHECK(composeSmsText("", true, "X1", EventPosition()) ==
        "ARGUS ALERTA CRITICA: desconocido, sin manos en el volante. Camion X1. "
        "Ubicacion desconocida.");
}

static void theVehicleIsMadeSafeAndNeverEmpty() {
  CHECK(composeSmsText("eyes_closed", true, "", EventPosition()) ==
        "ARGUS ALERTA CRITICA: ojos cerrados, sin manos en el volante. Camion desconocido. "
        "Ubicacion desconocida.");
  CHECK(composeSmsText("eyes_closed", true, "AB_12@\xC3\x91", EventPosition()) ==
        "ARGUS ALERTA CRITICA: ojos cerrados, sin manos en el volante. Camion AB 12   . "
        "Ubicacion desconocida.");
}

static void neverLongerThan160() {
  // The longest legal raw alert type (64 chars) and an over-long vehicle, with the longest link.
  std::string longType(64, 'r');
  std::string text = composeSmsText(longType, true, std::string(200, 'V'), at(-89.999999, -179.999999));
  CHECK(text.size() == kSmsMaxLength);
  const std::string link = "https://maps.google.com/?q=-89.999999,-179.999999";
  CHECK(text.size() >= link.size() && text.compare(text.size() - link.size(), link.size(), link) == 0);
  CHECK(text.find(", sin manos en el volante. Camion " + std::string(kSmsVehicleMaxLength, 'V') +
                  ". ") != std::string::npos);
  CHECK(text.compare(0, 22, "ARGUS ALERTA CRITICA: ") == 0);
  // The longest real texts fit with no cut at all.
  std::string real = composeSmsText("head_down_eyes_closed", true, "64b0a0000000000000000001",
                                    at(-89.999999, -179.999999));
  CHECK(real.size() <= kSmsMaxLength);
  CHECK(real.find("cabeza abajo y ojos cerrados, sin manos en el volante. ") != std::string::npos);
  for (char c : real) CHECK(c >= 0x20 && c < 0x7F);  // plain ASCII, nothing for Ctrl-Z to trip on
}

// --- Parity with the backend alert (route guardian contracts G9): the time ---

static void theTimeGoesAfterTheTruck() {
  CHECK(composeSmsText("eyes_closed", true, "JAL-123-A", kHere, "14:05") ==
        "ARGUS ALERTA CRITICA: ojos cerrados, sin manos en el volante. Camion JAL-123-A, 14:05. "
        "https://maps.google.com/?q=20.659712,-103.349612");
  CHECK(composeSmsText("eyes_closed", false, "JAL-123-A", kHere, "14:05") ==
        "ARGUS ALERTA CRITICA: ojos cerrados. Camion JAL-123-A, 14:05. "
        "https://maps.google.com/?q=20.659712,-103.349612");
  CHECK(composeSmsText("no_face", false, "JAL-123-A", EventPosition(), "07:30") ==
        "ARGUS ALERTA CRITICA: conductor no visible. Camion JAL-123-A, 07:30. "
        "Ubicacion desconocida.");
  CHECK(composeSmsText("no_grip", true, "JAL-123-A", kHere, "23:59") ==
        "ARGUS ALERTA CRITICA: sin manos en el volante. Camion JAL-123-A, 23:59. "
        "https://maps.google.com/?q=20.659712,-103.349612");
  CHECK(composeSmsText("no_grip", true, "", EventPosition(), "00:00") ==
        "ARGUS ALERTA CRITICA: sin manos en el volante. Camion desconocido, 00:00. "
        "Ubicacion desconocida.");
  // No GPS time: no time part, the text of before.
  CHECK(composeSmsText("head_down", true, "JAL-123-A", kHere, "") ==
        composeSmsText("head_down", true, "JAL-123-A", kHere));
}

static void everyMotiveWithTheTime() {
  const char* types[] = {"eyes_closed", "yawn_eyes_closed", "head_down", "head_down_eyes_closed",
                         "no_face"};
  for (const char* t : types) {
    for (bool handsOff : {true, false}) {
      std::string text = composeSmsText(t, handsOff, "JAL-123-A", kHere, "14:05");
      CHECK(text == std::string("ARGUS ALERTA CRITICA: ") + smsMotivo(t) +
                        (handsOff ? ", sin manos en el volante" : "") +
                        ". Camion JAL-123-A, 14:05. "
                        "https://maps.google.com/?q=20.659712,-103.349612");
      CHECK(text.size() <= kSmsMaxLength);
    }
  }
}

// The cut rule: the time goes only when the whole motive fits with it.
static void theTimeIsLeftOutBeforeTheMotiveIsCut() {
  // The longest real Mexican text keeps it: the longest motive, the hands-off part, a 16-char
  // plate and a 3-digit longitude.
  std::string mx = composeSmsText("head_down_eyes_closed", true, "ABCDEFGHIJKLMNOP",
                                  at(32.534567, -117.123456), "14:05");
  CHECK(mx.size() <= kSmsMaxLength);
  CHECK(mx.find("Camion ABCDEFGHIJKLMNOP, 14:05. ") != std::string::npos);
  CHECK(mx.find("cabeza abajo y ojos cerrados, ") != std::string::npos);
  // A truck id and the longest link don't leave room: the time is dropped, nothing else is cut.
  std::string worst = composeSmsText("head_down_eyes_closed", true, "64b0a0000000000000000001",
                                     at(-89.999999, -179.999999), "14:05");
  CHECK(worst == composeSmsText("head_down_eyes_closed", true, "64b0a0000000000000000001",
                                at(-89.999999, -179.999999)));
  CHECK(worst.find("14:05") == std::string::npos);
  CHECK(worst.size() <= kSmsMaxLength);
  // A long raw alert type: no time, the motive cut to exactly 160.
  std::string longType = composeSmsText(std::string(64, 'r'), true, std::string(200, 'V'),
                                        at(-89.999999, -179.999999), "14:05");
  CHECK(longType.size() == kSmsMaxLength);
  CHECK(longType.find("14:05") == std::string::npos);
  // A clock with anything odd is made GSM-safe like the rest.
  CHECK(composeSmsText("eyes_closed", false, "X1", EventPosition(), "1_:0@") ==
        "ARGUS ALERTA CRITICA: ojos cerrados. Camion X1, 1 :0 . Ubicacion desconocida.");
}

static void theClock() {
  CHECK(smsClockText(0, -360).empty());                 // no GPS time
  CHECK(smsClockText(1793736330ULL, 0) == "20:05");     // 2026-11-03 20:05:30 UTC
  CHECK(smsClockText(1793736330ULL, -360) == "14:05");  // Mexico central, UTC-6
  CHECK(smsClockText(1793761200ULL, -360) == "21:00");  // 03:00 UTC: the evening before
  CHECK(smsClockText(1793761200ULL, 330) == "08:30");   // a half-hour zone
  // The event's epoch from the GPS clock, before or after the GPS read, across a millis() wrap.
  CHECK(smsEventEpoch(0, 1000, 5000) == 0);
  CHECK(smsEventEpoch(1793736330ULL, 1000, 6000) == 1793736335ULL);
  CHECK(smsEventEpoch(1793736330ULL, 61000, 1000) == 1793736270ULL);  // a minute before it
  CHECK(smsEventEpoch(1793736330ULL, 0xFFFFF000u, 0x00001000u) == 1793736338ULL);
}

static void whyAnSmsIsSkipped() {
  CHECK(smsSkipReason(SmsQueued::Queued) == nullptr);
  CHECK(smsSkipReason(SmsQueued::Ignored) == nullptr);
  CHECK(std::string(smsSkipReason(SmsQueued::NoPhone)).find("ARGUS_ALERT_PHONE") !=
        std::string::npos);
  CHECK(std::string(smsSkipReason(SmsQueued::Duplicate)).find("already had its SMS") !=
        std::string::npos);
  // A queued result carries what the log composes its text from.
  SmsPolicy s;
  SmsQueueResult r = s.onCameraCritical("ep1", "eyes_closed", false, kHere, true, 1234);
  CHECK(r.result == SmsQueued::Queued && !r.handsOff && r.queuedAtMs == 1234);
  r = s.onGripCritical("grip-0000abcd-1", kHere, true, 99);
  CHECK(r.result == SmsQueued::Queued && r.handsOff && r.queuedAtMs == 99);
}

// --- The two sources since 2026-11-03 ---

static void aCameraCriticalWithTheHandsOnSaysSo() {
  SmsPolicy s;
  CHECK(s.onCameraCritical("ep1", "eyes_closed", /*handsOff=*/false, kHere, true, 0).result ==
        SmsQueued::Queued);
  CHECK(!s.slot(0).handsOff);
  CHECK(composeSmsText("eyes_closed", false, "JAL-123-A", kHere) ==
        "ARGUS ALERTA CRITICA: ojos cerrados. Camion JAL-123-A. "
        "https://maps.google.com/?q=20.659712,-103.349612");
  CHECK(composeSmsText("no_face", false, "JAL-123-A", EventPosition()) ==
        "ARGUS ALERTA CRITICA: conductor no visible. Camion JAL-123-A. Ubicacion desconocida.");
}

static void aGripEpisodeQueuesOnceWithItsOwnText() {
  SmsPolicy s;
  CHECK(s.onGripCritical("grip-0000abcd-1", kHere, true, 0).result == SmsQueued::Queued);
  CHECK(s.slot(0).episode == "grip-0000abcd-1");
  CHECK(s.slot(0).alertType == "no_grip");
  CHECK(s.slot(0).handsOff);
  CHECK(s.onGripCritical("grip-0000abcd-1", kHere, true, 10).result == SmsQueued::Duplicate);
  CHECK(s.onGripCritical("grip-0000abcd-2", kHere, true, 20).result == SmsQueued::Queued);
  CHECK(s.onGripCritical("grip-0000abcd-3", kHere, false, 30).result == SmsQueued::NoPhone);
  CHECK(composeSmsText("no_grip", true, "JAL-123-A", kHere) ==
        "ARGUS ALERTA CRITICA: sin manos en el volante. Camion JAL-123-A. "
        "https://maps.google.com/?q=20.659712,-103.349612");
  CHECK(composeSmsText("no_grip", true, "", EventPosition()) ==
        "ARGUS ALERTA CRITICA: sin manos en el volante. Camion desconocido. "
        "Ubicacion desconocida.");
}

// A burst of grip episodes never pushes a camera episode out of its ring (a re-served critical
// must not text twice), and the two never collide.
static void gripAndCameraEpisodesHaveTheirOwnRings() {
  SmsPolicy s;
  CHECK(both(s, "ep1", 0, "eyes_closed", false).result == SmsQueued::NoPhone);
  for (int i = 0; i < 3 * SmsPolicy::kRemembered; i++) {
    std::string g = "grip-0000abcd-" + std::to_string(i);
    CHECK(s.onGripCritical(g, kHere, false, i).result == SmsQueued::NoPhone);
  }
  CHECK(both(s, "ep1", 100, "eyes_closed", false).result == SmsQueued::Duplicate);
}

int main() {
  RUN(aCameraCriticalQueuesOncePerEpisode);
  RUN(noFaceIsQueuedLikeAnyOtherCritical);
  RUN(withNoPhoneAnEpisodeIsSkippedAndReportedOnce);
  RUN(theRingRemembersTheLastEightEpisodes);
  RUN(aFullQueueDropsTheOldestWaitingMessage);
  RUN(oldestDueFirstAndRetriesTenSecondsApart);
  RUN(aMessageWaitingFiveMinutesIsDropped);
  RUN(dropRemovesAMessageWithoutAnAttempt);
  RUN(survivesMillisWrap);
  RUN(theTextWithAPosition);
  RUN(theTextWithoutAPosition);
  RUN(everyMotive);
  RUN(theVehicleIsMadeSafeAndNeverEmpty);
  RUN(neverLongerThan160);
  RUN(theTimeGoesAfterTheTruck);
  RUN(everyMotiveWithTheTime);
  RUN(theTimeIsLeftOutBeforeTheMotiveIsCut);
  RUN(theClock);
  RUN(whyAnSmsIsSkipped);
  RUN(aCameraCriticalWithTheHandsOnSaysSo);
  RUN(aGripEpisodeQueuesOnceWithItsOwnText);
  RUN(gripAndCameraEpisodesHaveTheirOwnRings);
  return report("sms_policy_host_test");
}
