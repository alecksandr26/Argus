// 1:1 mirror of the corner-case section of
// src/cv-argus/tests/test_fusion_orchestrator_decision.py (same inputs, same expected outputs),
// against the C++ port. See that file for the reasoning behind each case.
// SOURCES: fusion.cpp
#include <string>

#include "check.h"
#include "fusion.h"

static FusionConfig cfg() {
  FusionConfig c;
  c.debounceSeconds = 2.0;
  c.escalationSeconds = 10.0;
  c.recoverySeconds = 4.0;
  return c;
}
static FusionReading rd(DrowsyState d, GripState g, double at) { return {d, g, at}; }
static FusionReading good(double at) { return rd(DrowsyState::NOT_DROWSY, GripState::GOOD, at); }
static FusionReading mediumGrip(double at) { return rd(DrowsyState::NOT_DROWSY, GripState::BAD, at); }
static FusionReading mediumDrowsy(double at) { return rd(DrowsyState::DROWSY, GripState::GOOD, at); }
static FusionReading critical(double at) { return rd(DrowsyState::DROWSY, GripState::BAD, at); }

static FusionDecision confirmMediumAndRecover(FusionOrchestrator& f) {
  f.update(mediumGrip(0.0));
  f.update(mediumGrip(2.0));
  f.recordAlertPosted("root");
  f.update(good(3.0));
  return f.update(good(7.0));
}

static void failedResolveIsOwedAndRetried() {
  FusionOrchestrator f(cfg());
  CHECK(confirmMediumAndRecover(f).resolvedIncidentId == "root");
  CHECK(f.update(good(10.0)).resolvedIncidentId == "root");
  f.recordResolvePosted("root");
  CHECK(f.update(good(11.0)).resolvedIncidentId.empty());
}

static void alertPostedAfterRecoveryOwesResolve() {
  FusionOrchestrator f(cfg());
  f.update(mediumGrip(0.0));
  CHECK(f.update(mediumGrip(2.0)).alert.present);
  f.update(good(3.0));
  f.update(good(7.0));
  f.recordAlertPosted("late");
  CHECK(f.update(good(20.0)).resolvedIncidentId == "late");
}

static void olderReadingIgnored() {
  FusionOrchestrator f(cfg());
  f.update(mediumGrip(0.0));
  f.update(mediumGrip(2.0));
  f.recordAlertPosted("root");
  f.update(good(3.0));
  f.update(mediumGrip(1.0));
  CHECK(f.update(good(7.0)).resolvedIncidentId == "root");
}

static void gapRestartsDebounce() {
  FusionConfig c = cfg();
  c.maxGapSeconds = 6.0;
  FusionOrchestrator f(c);
  f.update(mediumGrip(0.0));
  CHECK(!f.update(mediumGrip(10.0)).alert.present);
  CHECK(f.update(mediumGrip(12.0)).alert.present);
}

static void tickFiresGripOnlyIncident() {
  FusionConfig c = cfg();
  c.staleAfterSeconds = 30.0;
  FusionOrchestrator f(c);
  f.update(good(0.0));
  f.tick(0.5, GripState::BAD);
  FusionDecision d = f.tick(2.5, GripState::BAD);
  CHECK(d.alert.present);
  CHECK(d.alert.severityLevel == Severity::Medium);
  CHECK(d.alert.gripStatus == GripState::BAD);
}

static void staleCameraReportsOffline() {
  FusionConfig c = cfg();
  c.staleAfterSeconds = 3.0;
  FusionOrchestrator f(c);
  f.update(mediumDrowsy(0.0));
  FusionDecision d = f.tick(5.0, GripState::BAD);
  CHECK(d.cameraOffline);
  CHECK(!d.alert.present);
}

static void noFaceSustainedCountsAsDrowsy() {
  FusionConfig c = cfg();
  c.noFaceDrowsyAfterSeconds = 3.0;
  FusionOrchestrator f(c);
  int alerts = 0;
  FusionDecision last;
  for (int t = 0; t < 6; t++) {
    last = f.update(rd(DrowsyState::NO_FACE, GripState::GOOD, t));
    if (last.alert.present) alerts++;
  }
  CHECK(alerts == 1);
  CHECK(last.alert.present && last.alert.severityLevel == Severity::Medium);
}

static void snapshotRestoreResumesIncident() {
  FusionOrchestrator f(cfg());
  f.update(mediumGrip(0.0));
  f.update(mediumGrip(2.0));
  f.recordAlertPosted("root");
  FusionSnapshot snap = f.snapshot();
  FusionOrchestrator rebooted(cfg());
  rebooted.restore(snap);
  rebooted.update(good(3.0));
  CHECK(rebooted.update(good(7.0)).resolvedIncidentId == "root");
}

static void snapshotKeepsOwedAlert() {
  FusionOrchestrator f(cfg());
  f.update(critical(0.0));
  f.update(critical(2.0));
  FusionOrchestrator rebooted(cfg());
  rebooted.restore(f.snapshot());
  CHECK(rebooted.update(critical(3.0)).alert.present);
}

static void epochScaleTimestampsKeepSubSecondPrecision() {
  // Pi mono_ms / 1000 after weeks of uptime: a float would round to whole seconds here.
  FusionOrchestrator f(cfg());
  double t0 = 4294967.0;
  f.update(critical(t0));
  CHECK(!f.update(critical(t0 + 1.9)).alert.present);
  CHECK(f.update(critical(t0 + 2.0)).alert.present);
}

int main() {
  RUN(failedResolveIsOwedAndRetried);
  RUN(alertPostedAfterRecoveryOwesResolve);
  RUN(olderReadingIgnored);
  RUN(gapRestartsDebounce);
  RUN(tickFiresGripOnlyIncident);
  RUN(staleCameraReportsOffline);
  RUN(noFaceSustainedCountsAsDrowsy);
  RUN(snapshotRestoreResumesIncident);
  RUN(snapshotKeepsOwedAlert);
  RUN(epochScaleTimestampsKeepSubSecondPrecision);
  return report("fusion_corner_host_test");
}
