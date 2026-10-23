// What the ESP32 does with each record of a PULL / DLQ session and the report lines that end it
// (docs/designs/final-tweaks-contracts.md C1.3, C1.8), from real record lines through
// parsePiRecord() to the Bluetooth lines.
// SOURCES: pull_session.cpp sender_policy.cpp backend_messages.cpp pi_event_policy.cpp pi_record.cpp gps_track.cpp pi_protocol.cpp
// NEEDS: arduinojson
#include <ArduinoJson.h>

#include <string>

#include "check.h"
#include "pull_session.h"

static std::string line(const char* id, const char* severity, const char* previous,
                        bool transition, const char* ageS = "0.4", const char* extra = "") {
  return std::string(R"({"id":")") + id +
         R"(","kind":"drowsiness","level":2,"created_at_ms":1,"source_id":"c","age_s":)" + ageS +
         R"(,"payload":{"class_name":"Drowsy","probabilities":[0.1,0.9],"transition":)" +
         (transition ? "true" : "false") + R"(,"severity":")" + severity +
         R"(","previous_severity":")" + previous +
         R"(","episode":"ep1","reason":"eyes_closed","model":"parts-rules-v1"})" + extra + "}";
}

static HerePosition here() {
  HerePosition h;
  h.position.known = true;
  h.position.lat = 20.5;
  h.position.lon = -103.25;
  h.hasSpeed = true;
  h.speedKmh = 60;
  return h;
}

struct Fixture {
  SenderPolicy sender;
  VigilanceTracker vigilance;
  SessionReport report;

  RecordDecision feed(const std::string& text, bool dlq, uint32_t now,
                      const HerePosition& h = here()) {
    PiRecord r;
    ParseOutcome parsed = parsePiRecord(text, r);
    RecordDecision d = decidePulledRecord(parsed, r, dlq, sender, vigilance, h, now);
    addRecordDecision(report, r.id, d);
    return d;
  }
};

static void keepalivesAreAckedAndSetTheVigilance() {
  Fixture f;
  RecordDecision d = f.feed(line("k1", "medium", "medium", false), false, 1000);
  CHECK(d.action == RecordAction::Ack);
  CHECK(d.kind == PiEventKind::None);
  CHECK(std::string(f.vigilance.vigilance(1000)) == "medium");
  CHECK(f.report.acks.size() == 1 && f.report.acks[0] == "k1");
  CHECK(!f.sender.livePending());
}

static void anEventIsTakenWithThePositionWhereItWasPulled() {
  Fixture f;
  RecordDecision d = f.feed(line("c1", "critical", "medium", true, "1.5"), false, 1000);
  CHECK(d.action == RecordAction::Take);
  CHECK(d.kind == PiEventKind::AlertCritical);
  CHECK(f.report.taken.size() == 1 && f.report.taken[0] == "c1");
  const HeldEvent& e = f.sender.liveSlot(0);
  CHECK(e.used && e.recordId == "c1" && e.tier == EventTier::Critical);
  CHECK(e.event.severity == "critical");
  CHECK(e.event.alertType == "eyes_closed");
  CHECK(e.event.episode == "ep1");
  CHECK(e.event.position.known);
  CHECK_NEAR(e.event.position.lat, 20.5, 1e-9);
  CHECK_NEAR(e.ageS(3000), 3.5, 1e-3);  // 1.5 s old when pulled + 2 s held
  CHECK(std::string(f.vigilance.vigilance(1000)) == "critical");
}

static void mediumAndDriverOkAreTakenToo() {
  Fixture f;
  CHECK(f.feed(line("m1", "medium", "none", true), false, 0).kind == PiEventKind::AlertMedium);
  RecordDecision ok = f.feed(line("o1", "none", "medium", true), false, 10);
  CHECK(ok.kind == PiEventKind::DriverOk);
  CHECK(ok.action == RecordAction::Take);
  CHECK(f.report.taken.size() == 2);
}

static void anEventWithNoAgeIsFailedTooOld() {
  Fixture f;
  RecordDecision d = f.feed(line("c1", "critical", "none", true, "null"), false, 0);
  CHECK(d.action == RecordAction::FailTooOld);
  CHECK(f.report.fails.size() == 1);
  CHECK(f.report.fails[0].id == "c1" && f.report.fails[0].reason == "too_old");
  CHECK(f.report.fails[0].body.empty());
  CHECK(!f.sender.livePending());
  // A keepalive with no age is still just ACKed.
  CHECK(f.feed(line("k1", "none", "none", false, "null"), false, 0).action == RecordAction::Ack);
}

static void unreadableRowsAreAckedAwayOnlyWithAnId() {
  Fixture f;
  CHECK(f.feed(R"({"id":"h1","kind":"route_status","payload":{}})", false, 0).action ==
        RecordAction::Ack);
  CHECK(f.feed(R"({"id":"t1","kind":"drows)", false, 0).action == RecordAction::Leave);
  CHECK(f.report.acks.size() == 1 && f.report.acks[0] == "h1");
}

static void aRowServedAgainIsTakenAgainButHeldOnce() {
  Fixture f;
  f.feed(line("c1", "critical", "none", true), false, 0);
  RecordDecision d = f.feed(line("c1", "critical", "none", true), false, 130000);  // lease lapsed
  CHECK(d.action == RecordAction::Take);
  CHECK(d.take == TakeResult::AlreadyHeld);
  CHECK(f.report.taken.size() == 2);
}

static void aFullSenderLeavesTheEventOnThePi() {
  Fixture f;
  for (int i = 0; i < SenderPolicy::kLiveSlots; i++) {
    std::string id = "m" + std::to_string(i);
    f.feed(line(id.c_str(), "medium", "none", true), false, i);
  }
  RecordDecision d = f.feed(line("c9", "critical", "none", true), false, 100);
  CHECK(d.action == RecordAction::Leave);
  CHECK(d.take == TakeResult::Full);
  CHECK(f.report.taken.size() == (size_t)SenderPolicy::kLiveSlots);
}

static void aDeadLetterReplaysItsStoredEvent() {
  Fixture f;
  std::string stored =
      R"({"client_event_id":"d1","episode":"ep1","kind":"alert","severity":"critical",)"
      R"("alert_type":"head_down","age_s":1.0,"delivery":"live","queued_seconds":0,)"
      R"("position":{"lat":21.25,"lon":-102.5,"last_known":false,"location_age_s":0},)"
      R"("speed":70.5,"ai_metadata":{"model":"parts-rules-v1","scores":null}})";
  std::string extra = R"(,"dlq":{"error":"http_503","body":)" + stored +
                      R"(,"failed_at_ms":5,"attempts":2,"queued_s":37})";
  RecordDecision d = f.feed(line("d1", "critical", "medium", true, "95.0", extra.c_str()), true,
                            1000, HerePosition());
  CHECK(d.action == RecordAction::Take);
  CHECK(f.sender.dlqHeld());
  const HeldEvent& e = f.sender.dlqSlot();
  CHECK(e.event.alertType == "head_down");     // as stored, not re-derived
  CHECK_NEAR(e.event.position.lat, 21.25, 1e-6);  // where it happened, not where the truck is now
  CHECK(e.priorAttempts == 2);
  CHECK_NEAR(e.ageS(2000), 96.0, 1e-3);
  CHECK_NEAR(e.queuedS(2000), 38.0, 1e-3);
  // DLQ rows never move the live vigilance.
  CHECK(std::string(f.vigilance.vigilance(1000)) == "unknown");
}

static void aDeadLetterWithNoStoredEventHasNoPosition() {
  Fixture f;
  std::string extra =
      R"(,"dlq":{"error":"esp32_unreachable","body":null,"failed_at_ms":5,"attempts":0,"queued_s":4})";
  f.feed(line("d1", "critical", "none", true, "70.0", extra.c_str()), true, 0, here());
  const HeldEvent& e = f.sender.dlqSlot();
  CHECK(e.used);
  CHECK(!e.event.position.known);  // "position unknown": never the current one
  CHECK(e.event.severity == "critical");
  CHECK(e.event.clientEventId == "d1");
}

static void aV1DeadLetterKeepsItsPosition() {
  Fixture f;
  std::string extra =
      R"(,"dlq":{"error":"no_network","body":{"alert_type":"eyes_closed","severity_level":"critical",)"
      R"("source":"fusion","coordinates":{"lat":19.5,"lon":-99.25},"speed_at_event":40,)"
      R"("client_event_id":"d1"},"failed_at_ms":5,"attempts":1,"queued_s":600})";
  f.feed(line("d1", "critical", "none", true, "700.0", extra.c_str()), true, 0, here());
  const HeldEvent& e = f.sender.dlqSlot();
  CHECK(e.event.position.known);
  CHECK_NEAR(e.event.position.lat, 19.5, 1e-6);
  CHECK(e.event.hasSpeed);
}

static void aDeadLetterWithNoAgeIsFailedTooOld() {
  Fixture f;
  std::string extra = R"(,"dlq":{"error":"x","body":null,"failed_at_ms":5,"attempts":1,"queued_s":4})";
  RecordDecision d = f.feed(line("d1", "critical", "none", true, "null", extra.c_str()), true, 0);
  CHECK(d.action == RecordAction::FailTooOld);
  CHECK(!f.sender.dlqHeld());
}

static void finishedEventsRideTheNextSession() {
  Fixture f;
  f.feed(line("c1", "critical", "none", true), false, 0);
  f.feed(line("m1", "medium", "none", true), false, 0);
  SendPlan p = f.sender.plan(0, true);
  f.sender.beginAttempt(p, 0);
  f.sender.endAttempt(AttemptEnd::Delivered, "", 10);
  f.sender.expire(30000);  // the medium's window ran out

  SessionReport next;
  addPendingOutcomes(next, f.sender);
  CHECK(next.outcomesIncluded == 2);
  CHECK(next.acks.size() == 1 && next.acks[0] == "c1");
  CHECK(next.fails.size() == 1 && next.fails[0].id == "m1" &&
        next.fails[0].reason == "retry_window");
  std::vector<ScriptLine> script = buildReportScript(next.acks, next.fails, next.taken);
  CHECK(script[0].line == "FAIL m1 retry_window -");
  CHECK(script[1].line == "ACK c1");
  CHECK(script.back().line == "DONE");
  f.sender.dropOutcomes(next.outcomesIncluded);  // the Pi answered OK
  CHECK(f.sender.outcomeCount() == 0);
}

static void aCriticalDeadLetteredLiveCarriesItsEventInTheFail() {
  Fixture f;
  f.feed(line("c1", "critical", "none", true), false, 0);
  f.sender.expire(60000);
  SessionReport next;
  addPendingOutcomes(next, f.sender);
  CHECK(next.fails.size() == 1);
  std::string fail = formatFailLine(next.fails[0].id, next.fails[0].reason, next.fails[0].body);
  CHECK(fail.compare(0, 23, "FAIL c1 retry_window {\"") == 0);
  JsonDocument d;
  CHECK(deserializeJson(d, next.fails[0].body) == DeserializationError::Ok);
  CHECK(d["client_event_id"] == "c1");
  CHECK_NEAR(d["position"]["lat"].as<double>(), 20.5, 1e-6);
}

int main() {
  RUN(keepalivesAreAckedAndSetTheVigilance);
  RUN(anEventIsTakenWithThePositionWhereItWasPulled);
  RUN(mediumAndDriverOkAreTakenToo);
  RUN(anEventWithNoAgeIsFailedTooOld);
  RUN(unreadableRowsAreAckedAwayOnlyWithAnId);
  RUN(aRowServedAgainIsTakenAgainButHeldOnce);
  RUN(aFullSenderLeavesTheEventOnThePi);
  RUN(aDeadLetterReplaysItsStoredEvent);
  RUN(aDeadLetterWithNoStoredEventHasNoPosition);
  RUN(aV1DeadLetterKeepsItsPosition);
  RUN(aDeadLetterWithNoAgeIsFailedTooOld);
  RUN(finishedEventsRideTheNextSession);
  RUN(aCriticalDeadLetteredLiveCarriesItsEventInTheFail);
  return report("pull_session_host_test");
}
