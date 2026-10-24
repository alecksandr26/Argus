// The critical-alert SMS (grip-alarm contracts C5): which criticals get one (one per episode, the
// ring of Pi episodes, no medium/OK/DLQ, no_face while SMS_ON_NO_FACE), the queue of 4, retries,
// the pending timeout, and the exact text.
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

static SmsQueueResult piCritical(SmsPolicy& s, const char* episode, uint32_t now,
                                 const char* reason = "eyes_closed", bool hasPhone = true,
                                 bool taken = true, bool dlq = false) {
  return s.onPiRecord(PiEventKind::AlertCritical, dlq, taken, episode, reason, kHere, hasPhone,
                      now);
}

static int queued(const SmsPolicy& s) {
  int n = 0;
  for (int i = 0; i < SmsPolicy::kSlots; i++) n += s.slot(i).used ? 1 : 0;
  return n;
}

// --- Which criticals get an SMS ---

static void aLiveTakenCriticalIsQueuedOncePerEpisode() {
  SmsPolicy s;
  CHECK(piCritical(s, "ep1", 0).result == SmsQueued::Queued);
  CHECK(s.pending());
  CHECK(s.slot(0).episode == "ep1");
  CHECK(s.slot(0).alertType == "eyes_closed");
  CHECK(s.slot(0).position.known && s.slot(0).position.lat == 20.659712);
  // The same record served again, or the episode's later critical: nothing new.
  CHECK(piCritical(s, "ep1", 5000).result == SmsQueued::Duplicate);
  CHECK(piCritical(s, "ep1", 6000, "head_down").result == SmsQueued::Duplicate);
  CHECK(queued(s) == 1);
  // Even after its SMS went: the ring remembers the episode, not the queue.
  CHECK(s.attemptEnded("ep1", true, 7000) == SmsAttemptEnd::Sent);
  CHECK(!s.pending());
  CHECK(piCritical(s, "ep1", 125000).result == SmsQueued::Duplicate);
  CHECK(piCritical(s, "ep2", 130000).result == SmsQueued::Queued);
}

static void noSmsForAMediumAnOkADlqReplayOrARecordLeftOnThePi() {
  SmsPolicy s;
  CHECK(s.onPiRecord(PiEventKind::AlertMedium, false, true, "ep1", "eyes_closed", kHere, true,
                     0).result == SmsQueued::Ignored);
  CHECK(s.onPiRecord(PiEventKind::DriverOk, false, true, "ep1", "", kHere, true, 0).result ==
        SmsQueued::Ignored);
  CHECK(s.onPiRecord(PiEventKind::None, false, false, "ep1", "", kHere, true, 0).result ==
        SmsQueued::Ignored);
  CHECK(piCritical(s, "ep1", 0, "eyes_closed", true, true, /*dlq=*/true).result ==
        SmsQueued::Ignored);
  CHECK(piCritical(s, "ep1", 0, "eyes_closed", true, /*taken=*/false).result ==
        SmsQueued::Ignored);
  CHECK(!s.pending());
  CHECK(piCritical(s, "ep1", 10).result == SmsQueued::Queued);  // none of those used it up
}

static void noFaceGetsItsSmsWhileTheSwitchIsOn() {
  SmsPolicy s;  // SMS_ON_NO_FACE 1
  CHECK(piCritical(s, "ep1", 0, "no_face").result == SmsQueued::Queued);
  CHECK(piCritical(s, "ep1", 10, "eyes_closed").result == SmsQueued::Duplicate);

  SmsTiming off;
  off.onNoFace = false;
  SmsPolicy t(off);
  CHECK(piCritical(t, "ep1", 0, "no_face").result == SmsQueued::NoFace);
  CHECK(!t.pending());
  CHECK(piCritical(t, "ep1", 10, "eyes_closed").result == SmsQueued::Queued);  // still its SMS
}

static void withNoPhoneAnEpisodeIsSkippedAndReportedOnce() {
  SmsPolicy s;
  CHECK(piCritical(s, "ep1", 0, "eyes_closed", /*hasPhone=*/false).result == SmsQueued::NoPhone);
  CHECK(piCritical(s, "ep1", 10, "eyes_closed", false).result == SmsQueued::Duplicate);
  CHECK(!s.pending());
  CHECK(s.onGripEpisode("grip-1a2b3c4d-1", kHere, false, 20).result == SmsQueued::NoPhone);
  CHECK(!s.pending());
}

static void aGripEpisodeIsQueuedAsNoGrip() {
  SmsPolicy s;
  SmsQueueResult r = s.onGripEpisode("grip-1a2b3c4d-1", kHere, true, 0);
  CHECK(r.result == SmsQueued::Queued && !r.evicted);
  CHECK(s.slot(0).alertType == "no_grip");
  CHECK(s.onGripEpisode("grip-1a2b3c4d-1", kHere, true, 10).result == SmsQueued::Duplicate);
  CHECK(s.onGripEpisode("grip-1a2b3c4d-2", kHere, true, 20).result == SmsQueued::Queued);
  CHECK(queued(s) == 2);
}

static void theRingRemembersTheLastEightPiEpisodes() {
  SmsPolicy s;
  for (int i = 0; i < SmsPolicy::kRemembered; i++) {
    std::string ep = "ep" + std::to_string(i);
    CHECK(piCritical(s, ep.c_str(), i, "eyes_closed", false).result == SmsQueued::NoPhone);
  }
  CHECK(piCritical(s, "ep0", 100, "eyes_closed", false).result == SmsQueued::Duplicate);
  CHECK(piCritical(s, "ep8", 200, "eyes_closed", false).result == SmsQueued::NoPhone);
  CHECK(piCritical(s, "ep0", 300, "eyes_closed", false).result == SmsQueued::NoPhone);  // forgotten
  // Grip episodes don't push Pi episodes out of the ring.
  for (int i = 0; i < 20; i++) {
    std::string ep = "grip-1a2b3c4d-" + std::to_string(i);
    s.onGripEpisode(ep, kHere, false, 400);
  }
  CHECK(piCritical(s, "ep0", 500, "eyes_closed", false).result == SmsQueued::Duplicate);
}

// --- The queue ---

static void aFullQueueDropsTheOldestWaitingMessage() {
  SmsPolicy s;
  for (int i = 0; i < SmsPolicy::kSlots; i++) {
    std::string ep = "ep" + std::to_string(i);
    CHECK(piCritical(s, ep.c_str(), 1000 * i).result == SmsQueued::Queued);
  }
  SmsQueueResult r = s.onGripEpisode("grip-1a2b3c4d-1", kHere, true, 9000);
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
  CHECK(piCritical(s, "ep1", 1000).result == SmsQueued::Queued);
  CHECK(piCritical(s, "ep2", 2000).result == SmsQueued::Queued);
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
  CHECK(piCritical(s, "ep1", 1000).result == SmsQueued::Queued);  // e.g. no GSM registration
  CHECK(piCritical(s, "ep2", 61000).result == SmsQueued::Queued);
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
  CHECK(piCritical(s, "ep1", 0).result == SmsQueued::Queued);
  CHECK(s.drop("ep1"));
  CHECK(!s.drop("ep1"));
  CHECK(!s.pending());
  CHECK(piCritical(s, "ep1", 10).result == SmsQueued::Duplicate);  // still its one chance
}

static void survivesMillisWrap() {
  SmsPolicy s;
  const uint32_t t0 = 0xFFFFFF00u;
  CHECK(piCritical(s, "ep1", t0).result == SmsQueued::Queued);
  CHECK(s.attemptEnded("ep1", false, t0 + 100) == SmsAttemptEnd::Retry);
  CHECK(s.due(t0 + 10099) == -1);  // across the wrap
  CHECK(s.due(t0 + 10100) >= 0);
  SmsMessage dropped;
  CHECK(!s.expireOne(t0 + 299999, dropped));
  CHECK(s.expireOne(t0 + 300000, dropped));
}

// --- The text: exact strings ---

static void theTextWithAPosition() {
  CHECK(composeSmsText("eyes_closed", "JAL-123-A", kHere) ==
        "ARGUS ALERTA CRITICA: ojos cerrados. Camion JAL-123-A. "
        "https://maps.google.com/?q=20.659712,-103.349612");
  CHECK(composeSmsText("no_grip", "64b0a0000000000000000001", at(-33.5, 151.25)) ==
        "ARGUS ALERTA CRITICA: manos fuera del volante. Camion 64b0a0000000000000000001. "
        "https://maps.google.com/?q=-33.500000,151.250000");
}

static void theTextWithoutAPosition() {
  CHECK(composeSmsText("no_face", "JAL-123-A", EventPosition()) ==
        "ARGUS ALERTA CRITICA: conductor no visible. Camion JAL-123-A. Ubicacion desconocida.");
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
      {"no_grip", "manos fuera del volante"},
  };
  for (const Case& k : cases) {
    CHECK(std::string(smsMotivo(k.alertType)) == k.motivo);
    CHECK(composeSmsText(k.alertType, "X1", EventPosition()) ==
          std::string("ARGUS ALERTA CRITICA: ") + k.motivo + ". Camion X1. Ubicacion desconocida.");
  }
  CHECK(smsMotivo("mouth_open") == nullptr);
  // Anything else: the raw reason, made GSM-safe ('_' isn't the same character in GSM-7).
  CHECK(composeSmsText("mouth_open", "X1", EventPosition()) ==
        "ARGUS ALERTA CRITICA: mouth open. Camion X1. Ubicacion desconocida.");
  CHECK(composeSmsText("", "X1", EventPosition()) ==
        "ARGUS ALERTA CRITICA: desconocido. Camion X1. Ubicacion desconocida.");
}

static void theVehicleIsMadeSafeAndNeverEmpty() {
  CHECK(composeSmsText("eyes_closed", "", EventPosition()) ==
        "ARGUS ALERTA CRITICA: ojos cerrados. Camion desconocido. Ubicacion desconocida.");
  CHECK(composeSmsText("eyes_closed", "AB_12@\xC3\x91", EventPosition()) ==
        "ARGUS ALERTA CRITICA: ojos cerrados. Camion AB 12   . Ubicacion desconocida.");
}

static void neverLongerThan160() {
  // The longest legal raw alert type (64 chars) and an over-long vehicle, with the longest link.
  std::string longType(64, 'r');
  std::string text = composeSmsText(longType, std::string(200, 'V'), at(-89.999999, -179.999999));
  CHECK(text.size() == kSmsMaxLength);
  const std::string link = "https://maps.google.com/?q=-89.999999,-179.999999";
  CHECK(text.size() >= link.size() && text.compare(text.size() - link.size(), link.size(), link) == 0);
  CHECK(text.find(". Camion " + std::string(kSmsVehicleMaxLength, 'V') + ". ") !=
        std::string::npos);
  CHECK(text.compare(0, 22, "ARGUS ALERTA CRITICA: ") == 0);
  // The longest real texts fit with no cut at all.
  std::string real = composeSmsText("head_down_eyes_closed", "64b0a0000000000000000001",
                                    at(-89.999999, -179.999999));
  CHECK(real.size() <= kSmsMaxLength);
  CHECK(real.find("cabeza abajo y ojos cerrados. ") != std::string::npos);
  for (char c : real) CHECK(c >= 0x20 && c < 0x7F);  // plain ASCII, nothing for Ctrl-Z to trip on
}

int main() {
  RUN(aLiveTakenCriticalIsQueuedOncePerEpisode);
  RUN(noSmsForAMediumAnOkADlqReplayOrARecordLeftOnThePi);
  RUN(noFaceGetsItsSmsWhileTheSwitchIsOn);
  RUN(withNoPhoneAnEpisodeIsSkippedAndReportedOnce);
  RUN(aGripEpisodeIsQueuedAsNoGrip);
  RUN(theRingRemembersTheLastEightPiEpisodes);
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
  return report("sms_policy_host_test");
}
