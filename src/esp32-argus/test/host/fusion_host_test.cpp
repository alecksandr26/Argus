// Host-side tests for the firmware's FusionOrchestrator port (fusion.h/.cpp), compiled with
// g++ against shim/Arduino.h instead of the ESP32 toolchain. Mirrors the key cases in
// src/cv-argus/tests/test_fusion_orchestrator_decision.py so the C++ port can't silently
// drift from the Python reference. Run: ./run.sh
#include <cstdio>
#include <cstdlib>

#include "fusion.h"

static int failures = 0;
#define CHECK(cond)                                                     \
  do {                                                                  \
    if (!(cond)) {                                                      \
      std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);       \
      failures++;                                                       \
    }                                                                   \
  } while (0)

static FusionConfig cfg() {
  FusionConfig c;
  c.debounceSeconds = 2.0f;
  c.escalationSeconds = 10.0f;
  c.recoverySeconds = 4.0f;
  return c;
}
static FusionReading critical(float at) { return {DrowsyState::DROWSY, GripState::BAD, at}; }
static FusionReading mediumGrip(float at) { return {DrowsyState::NOT_DROWSY, GripState::BAD, at}; }
static FusionReading good(float at) { return {DrowsyState::NOT_DROWSY, GripState::GOOD, at}; }

static void blipDoesNotConfirm() {
  FusionOrchestrator f(cfg());
  CHECK(!f.update(critical(0.0f)).alert.present);
  CHECK(!f.update(critical(1.9f)).alert.present);
  CHECK(f.confirmedSeverity() == Severity::Low);
}

static void sustainedCriticalConfirmsAtDebounce() {
  FusionOrchestrator f(cfg());
  f.update(critical(0.0f));
  FusionDecision d = f.update(critical(2.0f));
  CHECK(d.alert.present);
  CHECK(d.alert.severityLevel == Severity::Critical);
  CHECK(!d.alert.isEscalation);
}

static void escalationLinksToPostedAlert() {
  FusionOrchestrator f(cfg());
  f.update(mediumGrip(0.0f));
  f.update(mediumGrip(2.0f));
  f.recordAlertPosted("a1");
  FusionDecision d = f.update(mediumGrip(12.0f));
  CHECK(d.alert.present);
  CHECK(d.alert.severityLevel == Severity::Critical);
  CHECK(d.alert.isEscalation);
  CHECK(d.alert.relatedAlertId == "a1");
}

static void recoveryResolvesLatestPostedId() {
  FusionOrchestrator f(cfg());
  f.update(critical(0.0f));
  f.update(critical(2.0f));
  f.recordAlertPosted("a1");
  CHECK(!f.update(good(3.0f)).hasResolvedIncident);
  FusionDecision d = f.update(good(7.0f));
  CHECK(d.hasResolvedIncident);
  CHECK(d.resolvedIncidentId == "a1");
  CHECK(f.confirmedSeverity() == Severity::Low);
}

static void steadyAfterPostedIsNoOp() {
  FusionOrchestrator f(cfg());
  f.update(critical(0.0f));
  f.update(critical(2.0f));
  f.recordAlertPosted("a1");
  CHECK(!f.update(critical(3.0f)).alert.present);
}

static void unpostedAlertIsReRaisedUntilPosted() {
  FusionOrchestrator f(cfg());
  f.update(critical(0.0f));
  CHECK(f.update(critical(2.0f)).alert.present);  // POST "fails": no recordAlertPosted
  FusionDecision retry = f.update(critical(3.0f));
  CHECK(retry.alert.present);
  CHECK(retry.alert.severityLevel == Severity::Critical);
  CHECK(f.update(good(4.0f)).alert.present);  // still owed while recovering
  f.recordAlertPosted("a1");
  CHECK(!f.update(good(5.0f)).alert.present);
}

int main() {
  blipDoesNotConfirm();
  sustainedCriticalConfirmsAtDebounce();
  escalationLinksToPostedAlert();
  recoveryResolvesLatestPostedId();
  steadyAfterPostedIsNoOp();
  unpostedAlertIsReRaisedUntilPosted();
  if (failures) {
    std::printf("%d check(s) failed\n", failures);
    return 1;
  }
  std::printf("all fusion host tests passed\n");
  return 0;
}
