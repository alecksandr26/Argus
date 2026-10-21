// What a Pi record means for the backend, from the Pi's own severity (no grip sensor yet).
// SOURCES: pi_event_policy.cpp
#include "check.h"
#include "pi_event_policy.h"

static PiRecord reading(const char* severity, const char* previous, bool transition = true,
                        const char* episode = "e1") {
  PiRecord r;
  r.id = "r";
  r.kind = PiRecordKind::Reading;
  r.severity = severity;
  r.previousSeverity = previous;
  r.transition = transition;
  r.episode = episode;
  return r;
}

static void heartbeatPostsStatus() {
  PiRecord r;
  r.kind = PiRecordKind::RouteStatus;
  EpisodeAlerts e;
  CHECK(decidePiEvent(r, e).action == EventAction::PostStatus);
}

static void keepalivesSendNothing() {
  EpisodeAlerts e;
  CHECK(decidePiEvent(reading("critical", "critical", false), e).action == EventAction::Nothing);
}

static void risingToMediumOrCriticalRaisesAnAlert() {
  EpisodeAlerts e;
  EventDecision d = decidePiEvent(reading("medium", "none"), e);
  CHECK(d.action == EventAction::RaiseAlert);
  CHECK(d.severity == PiSeverity::Medium);
  CHECK(d.relatedAlertId.empty());
  CHECK(decidePiEvent(reading("critical", "low"), e).action == EventAction::RaiseAlert);
}

static void escalationLinksToTheEpisodesAlert() {
  EpisodeAlerts e;
  e.remember("e1", "alert-1");
  EventDecision d = decidePiEvent(reading("critical", "medium"), e);
  CHECK(d.action == EventAction::RaiseAlert);
  CHECK(d.relatedAlertId == "alert-1");
}

static void criticalToMediumIsNotANewAlert() {
  // No de-escalation from the device: the incident stays at its highest severity.
  EpisodeAlerts e;
  CHECK(decidePiEvent(reading("medium", "critical"), e).action == EventAction::Nothing);
}

static void recoveryResolvesTheNewestAlertOfTheEpisode() {
  EpisodeAlerts e;
  e.remember("e1", "alert-1");
  e.remember("e1", "alert-2");  // the escalation
  EventDecision d = decidePiEvent(reading("none", "critical"), e);
  CHECK(d.action == EventAction::ResolveAlert);
  CHECK(d.resolveAlertId == "alert-2");
}

static void recoveryWithoutAKnownAlertHasNoId() {
  EpisodeAlerts e;
  EventDecision d = decidePiEvent(reading("low", "medium"), e);
  CHECK(d.action == EventAction::ResolveAlert);
  CHECK(d.resolveAlertId.empty());
}

static void noneLowChangesSendNothing() {
  EpisodeAlerts e;
  CHECK(decidePiEvent(reading("low", "none", true, ""), e).action == EventAction::Nothing);
  CHECK(decidePiEvent(reading("none", "low", true, ""), e).action == EventAction::Nothing);
}

static void unknownSeveritySendsNothing() {
  // An older cv-argus without the rules' severity: nothing to decide from.
  EpisodeAlerts e;
  CHECK(decidePiEvent(reading("", "", true), e).action == EventAction::Nothing);
}

static void episodeMemoryIsBounded() {
  EpisodeAlerts e;
  for (int i = 0; i < 20; i++) e.remember("ep" + std::to_string(i), "a" + std::to_string(i));
  std::string id;
  CHECK(!e.lookup("ep0", id));
  CHECK(e.lookup("ep19", id) && id == "a19");
  e.forget("ep19");
  CHECK(!e.lookup("ep19", id));
}

static void episodesSurviveARebootThroughNvs() {
  EpisodeAlerts e;
  e.remember("ep1", "alert-1");
  e.remember("ep2", "alert-2");
  EpisodeAlerts restored;
  CHECK(restored.deserialize(e.serialize()));
  std::string id;
  CHECK(restored.lookup("ep1", id) && id == "alert-1");
  CHECK(restored.lookup("ep2", id) && id == "alert-2");
  CHECK(!restored.deserialize("garbage"));
  CHECK(!restored.lookup("ep1", id));
  CHECK(restored.deserialize(EpisodeAlerts().serialize()));
}

static void backendSeverityMapping() {
  CHECK(std::string(backendSeverity(PiSeverity::None)) == "low");
  CHECK(std::string(backendSeverity(PiSeverity::Low)) == "low");
  CHECK(std::string(backendSeverity(PiSeverity::Medium)) == "medium");
  CHECK(std::string(backendSeverity(PiSeverity::Critical)) == "critical");
}

int main() {
  RUN(heartbeatPostsStatus);
  RUN(keepalivesSendNothing);
  RUN(risingToMediumOrCriticalRaisesAnAlert);
  RUN(escalationLinksToTheEpisodesAlert);
  RUN(criticalToMediumIsNotANewAlert);
  RUN(recoveryResolvesTheNewestAlertOfTheEpisode);
  RUN(recoveryWithoutAKnownAlertHasNoId);
  RUN(noneLowChangesSendNothing);
  RUN(unknownSeveritySendsNothing);
  RUN(episodeMemoryIsBounded);
  RUN(episodesSurviveARebootThroughNvs);
  RUN(backendSeverityMapping);
  return report("pi_event_policy_host_test");
}
