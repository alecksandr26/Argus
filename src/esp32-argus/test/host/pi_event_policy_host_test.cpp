// Which Pi records are backend events (docs/designs/final-tweaks-contracts.md C1.5), from the
// Pi's own severity (no grip sensor yet). The full table is also replayed from the shared
// vectors in event_vectors_host_test.cpp.
// SOURCES: pi_event_policy.cpp
#include "check.h"
#include "pi_event_policy.h"

static PiRecord reading(const char* severity, const char* previous, bool transition = true,
                        const char* episode = "e1") {
  PiRecord r;
  r.id = "r";
  r.severity = severity;
  r.previousSeverity = previous;
  r.transition = transition;
  r.episode = episode;
  return r;
}

static void keepalivesAreNotEvents() {
  CHECK(classifyPiEvent(reading("critical", "critical", false)) == PiEventKind::None);
  CHECK(classifyPiEvent(reading("none", "none", false)) == PiEventKind::None);
}

static void risingToMediumOrCriticalIsAnAlert() {
  CHECK(classifyPiEvent(reading("medium", "none")) == PiEventKind::AlertMedium);
  CHECK(classifyPiEvent(reading("critical", "low")) == PiEventKind::AlertCritical);
}

static void escalationIsOneCriticalAlert() {
  // One alert per incident: medium once, plus one critical upgrade (the backend links them by
  // episode, so the device no longer remembers alert ids).
  CHECK(classifyPiEvent(reading("critical", "medium")) == PiEventKind::AlertCritical);
}

static void criticalToMediumIsNotAnEvent() {
  // No de-escalation from the device: the incident stays at its highest severity.
  CHECK(classifyPiEvent(reading("medium", "critical")) == PiEventKind::None);
}

static void reasonChangeAtTheSameLevelSendsNothing() {
  CHECK(classifyPiEvent(reading("critical", "critical")) == PiEventKind::None);
  CHECK(classifyPiEvent(reading("medium", "medium")) == PiEventKind::None);
}

static void recoveryIsOneDriverOk() {
  CHECK(classifyPiEvent(reading("none", "critical")) == PiEventKind::DriverOk);
  CHECK(classifyPiEvent(reading("low", "medium")) == PiEventKind::DriverOk);
}

static void noneLowChangesAreNotEvents() {
  CHECK(classifyPiEvent(reading("low", "none", true, "")) == PiEventKind::None);
  CHECK(classifyPiEvent(reading("none", "low", true, "")) == PiEventKind::None);
}

static void missingPreviousCountsAsNone() {
  CHECK(classifyPiEvent(reading("medium", "")) == PiEventKind::AlertMedium);
  CHECK(classifyPiEvent(reading("none", "")) == PiEventKind::None);
}

static void unknownSeverityIsNotAnEvent() {
  // An older cv-argus without the rules' severity: nothing to decide from.
  CHECK(classifyPiEvent(reading("", "", true)) == PiEventKind::None);
  CHECK(classifyPiEvent(reading("severe", "none", true)) == PiEventKind::None);
}

static void onlyCriticalAlertsAreCriticalEvents() {
  CHECK(isCriticalEvent(PiEventKind::AlertCritical));
  CHECK(!isCriticalEvent(PiEventKind::AlertMedium));
  CHECK(!isCriticalEvent(PiEventKind::DriverOk));
  CHECK(!isCriticalEvent(PiEventKind::None));
}

static void vigilanceMapping() {
  CHECK(std::string(backendVigilance(PiSeverity::None)) == "low");
  CHECK(std::string(backendVigilance(PiSeverity::Low)) == "low");
  CHECK(std::string(backendVigilance(PiSeverity::Medium)) == "medium");
  CHECK(std::string(backendVigilance(PiSeverity::Critical)) == "critical");
  CHECK(std::string(backendVigilance(PiSeverity::Unknown)) == "unknown");
}

int main() {
  RUN(keepalivesAreNotEvents);
  RUN(risingToMediumOrCriticalIsAnAlert);
  RUN(escalationIsOneCriticalAlert);
  RUN(criticalToMediumIsNotAnEvent);
  RUN(reasonChangeAtTheSameLevelSendsNothing);
  RUN(recoveryIsOneDriverOk);
  RUN(noneLowChangesAreNotEvents);
  RUN(missingPreviousCountsAsNone);
  RUN(unknownSeverityIsNotAnEvent);
  RUN(onlyCriticalAlertsAreCriticalEvents);
  RUN(vigilanceMapping);
  return report("pi_event_policy_host_test");
}
