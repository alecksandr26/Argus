// No Face is not "not drowsy" (REC-18a, CV-04). cv-argus reports a driver who left the frame
// as class "No Face" with no probabilities. A driver slumping out of view mid-incident must
// never count toward recovery: the open incident stays open and nothing is resolved. Only a
// real Not Drowsy reading recovers it. Sustained No Face from a calm state is covered by
// fusion_corner_host_test's noFaceSustainedCountsAsDrowsy (REC-18b).
// SOURCES: fusion.cpp
#include <string>

#include "check.h"
#include "fusion.h"

static FusionConfig cfg() {
  FusionConfig c;
  c.debounceSeconds = 2.0;
  c.escalationSeconds = 10.0;
  c.recoverySeconds = 4.0;
  c.noFaceDrowsyAfterSeconds = 3.0;
  return c;
}
static FusionReading rd(DrowsyState d, GripState g, double at) { return {d, g, at}; }

static FusionOrchestrator withOpenMediumIncident() {
  FusionOrchestrator f(cfg());
  f.update(rd(DrowsyState::DROWSY, GripState::GOOD, 0.0));
  f.update(rd(DrowsyState::DROWSY, GripState::GOOD, 2.0));
  f.recordAlertPosted("m1");
  return f;
}

static void noFaceMidIncidentDoesNotRecover() {
  FusionOrchestrator f = withOpenMediumIncident();
  for (double t = 3.0; t <= 12.0; t += 1.0) {
    FusionDecision d = f.update(rd(DrowsyState::NO_FACE, GripState::GOOD, t));
    CHECK(!d.hasResolvedIncident);
    CHECK(d.severity != Severity::Low);
  }
}

static void noFaceThenRealRecoveryResolves() {
  FusionOrchestrator f = withOpenMediumIncident();
  f.update(rd(DrowsyState::NO_FACE, GripState::GOOD, 3.0));
  f.update(rd(DrowsyState::NOT_DROWSY, GripState::GOOD, 4.0));
  CHECK(f.update(rd(DrowsyState::NOT_DROWSY, GripState::GOOD, 8.0)).resolvedIncidentId == "m1");
}

static void noFaceInterruptsARecoveryInProgress() {
  FusionOrchestrator f = withOpenMediumIncident();
  f.update(rd(DrowsyState::NOT_DROWSY, GripState::GOOD, 3.0));  // recovery starts
  f.update(rd(DrowsyState::NO_FACE, GripState::GOOD, 5.0));     // driver gone again
  // 4 s after the recovery started, but it was interrupted: not resolved yet.
  CHECK(!f.update(rd(DrowsyState::NOT_DROWSY, GripState::GOOD, 7.0)).hasResolvedIncident);
}

int main() {
  RUN(noFaceMidIncidentDoesNotRecover);
  RUN(noFaceThenRealRecoveryResolves);
  RUN(noFaceInterruptsARecoveryInProgress);
  return report("no_face_fusion_host_test");
}
