// One sender-task attempt end to end, minus the I/O (sender_step.h): what a report carries, which
// points go back to the buffer, and how the backend's answer moves the event and the known route
// (contracts C1.8, C2.1, C2.3).
// SOURCES: sender_step.cpp sender_policy.cpp ack_policy.cpp backend_messages.cpp pi_event_policy.cpp pi_protocol.cpp gps_track.cpp
// NEEDS: arduinojson
#include <ArduinoJson.h>

#include <string>

#include "check.h"
#include "sender_step.h"

static ReportEvent ev(const char* id, const char* severity = "critical") {
  ReportEvent e;
  e.clientEventId = id;
  e.episode = "ep1";
  e.severity = severity;
  e.alertType = "eyes_closed";
  return e;
}

static void addPoints(TrackBuffer& t, int n, uint32_t fromMs) {
  for (int i = 0; i < n; i++) {
    TrackPoint p;
    p.atMs = fromMs + i * 1000;
    p.lat = 20.0 + i;
    p.lon = -103.0;
    p.vigilance = "low";
    t.add(p);
  }
}

static ReportReaction answer(int status, const char* body, bool sentEvent,
                             const char* transportReason = nullptr) {
  return reactToReport(status, body, sentEvent, transportReason);
}

static const char* CREATED =
    R"({"points":{"stored":3,"route":"r1","status":"ok"},)"
    R"("event":{"status":"created","reason":null,"id_alert":"a1"},"active_route":"r1"})";
static const char* POINTS_OK = R"({"points":{"stored":3,"route":"r1","status":"ok"},"event":null,"active_route":"r1"})";
static const char* NO_ROUTE = R"({"points":{"stored":0,"route":null,"status":"no_active_route"},"event":null,"active_route":null})";

static void nothingDueMeansNoAttempt() {
  SenderPolicy s;
  TrackBuffer t;
  PreparedReport rep;
  addPoints(t, 1, 0);
  CHECK(prepareReport(s, t, 0, rep));  // the first report goes at once
  finishReport(s, t, rep, false, answer(200, POINTS_OK, false), 100);
  addPoints(t, 1, 1000);
  CHECK(!prepareReport(s, t, 2000, rep));
  CHECK(rep.plan.kind == PlanKind::Wait);
  CHECK(rep.plan.waitMs == 3100);
  CHECK(t.size() == 1);  // nothing taken while waiting
  CHECK(!s.inFlight());
}

static void aPointsOnlyReportCarriesEveryPoint() {
  SenderPolicy s;
  TrackBuffer t;
  addPoints(t, 3, 1000);
  PreparedReport rep;
  CHECK(prepareReport(s, t, 4000, rep));
  CHECK(!rep.hasEvent);
  CHECK(rep.pointCount == 3);
  CHECK(t.size() == 0);
  JsonDocument d;
  CHECK(deserializeJson(d, rep.body) == DeserializationError::Ok);
  CHECK(d["points"].size() == 3);
  CHECK_NEAR(d["points"][0]["age_s"].as<float>(), 3.0, 1e-3);  // oldest first
  CHECK(d["event"].isNull());
  ReportResult r = finishReport(s, t, rep, false, answer(200, POINTS_OK, false), 4500);
  CHECK(std::string(r.result) == "ok");
  CHECK(s.route() == RouteKnowledge::Active);
  CHECK(reportLogLine(rep, "wifi", 500, r) == "[live] points x3 attempt 1 via wifi 0.5 s: ok");
}

static void aLivenessReportHasNoPoints() {
  SenderPolicy s;
  TrackBuffer t;
  PreparedReport rep;
  CHECK(prepareReport(s, t, 0, rep));
  CHECK(rep.pointCount == 0);
  JsonDocument d;
  CHECK(deserializeJson(d, rep.body) == DeserializationError::Ok);
  CHECK(d["points"].is<JsonArray>() && d["points"].size() == 0);  // `points: []`, never absent
  ReportResult r = finishReport(s, t, rep, false, answer(200, NO_ROUTE, false), 300);
  CHECK(s.route() == RouteKnowledge::None);
  CHECK(reportLogLine(rep, "gprs", 300, r) == "[live] liveness attempt 1 via gprs 0.3 s: ok");
  CHECK(!prepareReport(s, t, 29000, rep));
  CHECK(prepareReport(s, t, 30300, rep));
}

static void aFailedReportDropsItsPoints() {
  SenderPolicy s;
  TrackBuffer t;
  addPoints(t, 4, 0);
  PreparedReport rep;
  CHECK(prepareReport(s, t, 5000, rep));
  ReportResult r = finishReport(s, t, rep, false, answer(-1, "", false, "timeout"), 13000);
  CHECK(std::string(r.result) == "failed" && r.detail == "timeout");
  CHECK(t.size() == 0);  // user decision: the path has a gap
  CHECK(s.route() == RouteKnowledge::Unknown);
}

static void aPreemptedReportPutsItsPointsBack() {
  SenderPolicy s;
  TrackBuffer t;
  addPoints(t, 4, 0);
  PreparedReport rep;
  CHECK(prepareReport(s, t, 5000, rep));
  addPoints(t, 1, 5500);  // read while the report was on its way
  CHECK(s.takeLive("c1", ev("c1"), EventTier::Critical, 0.3f, 5600) == TakeResult::Queued);
  CHECK(s.shouldPreempt(5700));
  ReportResult r = finishReport(s, t, rep, true, ReportReaction(), 5700);
  CHECK(std::string(r.result) == "preempted");
  CHECK(t.size() == 5);  // the four, older, then the new one

  // The live critical goes next, at once, with all five points.
  CHECK(prepareReport(s, t, 5700, rep));
  CHECK(rep.hasEvent && rep.recordId == "c1");
  CHECK(rep.pointCount == 5);
  CHECK_NEAR(rep.points[0].lat, 20.0, 1e-9);
}

static void aDeliveredEventIsAckedToThePi() {
  SenderPolicy s;
  TrackBuffer t;
  addPoints(t, 2, 0);
  CHECK(s.takeLive("c1", ev("c1"), EventTier::Critical, 1.0f, 1000) == TakeResult::Queued);
  PreparedReport rep;
  CHECK(prepareReport(s, t, 1500, rep));
  CHECK(rep.hasEvent && !rep.dlq && rep.attemptNo == 1);
  JsonDocument d;
  CHECK(deserializeJson(d, rep.body) == DeserializationError::Ok);
  CHECK(d["event"]["client_event_id"] == "c1");
  CHECK(d["event"]["delivery"] == "live");
  CHECK_NEAR(d["event"]["age_s"].as<float>(), 1.5, 1e-3);
  CHECK(d["points"].size() == 2);
  ReportResult r = finishReport(s, t, rep, false, answer(200, CREATED, true), 2000);
  CHECK(std::string(r.result) == "delivered");
  CHECK(s.outcomeCount() == 1 && s.outcome(0).ack && s.outcome(0).recordId == "c1");
  CHECK(reportLogLine(rep, "gprs", 500, r) == "[live] critical c1 attempt 1 via gprs 0.5 s: delivered");
}

static void aRejectedEventIsFailedWithTheBackendsReason() {
  SenderPolicy s;
  TrackBuffer t;
  CHECK(s.takeLive("m1", ev("m1", "medium"), EventTier::Medium, 0, 0) == TakeResult::Queued);
  PreparedReport rep;
  CHECK(prepareReport(s, t, 0, rep));
  ReportResult r = finishReport(
      s, t, rep, false,
      answer(200,
             R"({"points":{"stored":0,"status":"route_finished"},"event":{"status":"rejected",)"
             R"("reason":"route_finished"},"active_route":null})",
             true),
      100);
  CHECK(std::string(r.result) == "rejected" && r.detail == "route_finished");
  CHECK(!s.outcome(0).ack && s.outcome(0).reason == "route_finished");
  CHECK(s.route() == RouteKnowledge::None);
}

static void aFailedEventRetriesAndCountsItsAttempts() {
  SenderPolicy s;
  TrackBuffer t;
  CHECK(s.takeLive("c1", ev("c1"), EventTier::Critical, 0, 0) == TakeResult::Queued);
  PreparedReport rep;
  CHECK(prepareReport(s, t, 0, rep));
  finishReport(s, t, rep, false, answer(503, "", true), 8000);
  CHECK(s.livePending());
  CHECK(!prepareReport(s, t, 8500, rep));  // the 1 s gap
  CHECK(prepareReport(s, t, 9000, rep));
  CHECK(rep.attemptNo == 2);
  CHECK(rep.plan.retry);  // the longer mobile timeout
  ReportResult r = finishReport(s, t, rep, false, answer(-1, "", true, "no_network"), 9100);
  CHECK(reportLogLine(rep, "none", 100, r) ==
        "[live] critical c1 attempt 2 via none 0.1 s: failed (no_network)");
}

static void aDlqReplayIsLoggedAsTheNthOfFive() {
  SenderPolicy s;
  TrackBuffer t;
  CHECK(s.takeDlq("3f2a9c1b2233", ev("3f2a9c1b2233"), 90, 30, 2, 0) == TakeResult::Queued);
  PreparedReport rep;
  CHECK(prepareReport(s, t, 0, rep));
  CHECK(rep.dlq && rep.attemptNo == 3);
  JsonDocument d;
  CHECK(deserializeJson(d, rep.body) == DeserializationError::Ok);
  CHECK(d["event"]["delivery"] == "dlq");
  CHECK_NEAR(d["event"]["queued_seconds"].as<float>(), 30.0, 1e-3);
  ReportResult r = finishReport(s, t, rep, false, answer(503, "", true), 7400);
  CHECK(reportLogLine(rep, "gprs", 7400, r) ==
        "[dlq] critical 3f2a9c1b attempt 3/5 via gprs 7.4 s: failed (http_503)");
  CHECK(!s.dlqHeld());  // one try: the Pi counts it
  CHECK(s.outcome(0).reason == "http_503");
}

static void unauthorizedForgetsTheRoute() {
  SenderPolicy s;
  TrackBuffer t;
  PreparedReport rep;
  CHECK(prepareReport(s, t, 0, rep));
  finishReport(s, t, rep, false, answer(200, POINTS_OK, false), 100);
  CHECK(s.route() == RouteKnowledge::Active);
  addPoints(t, 1, 200);
  CHECK(prepareReport(s, t, 5100, rep));
  ReportReaction a = answer(401, "", false);
  CHECK(a.recredential);
  finishReport(s, t, rep, false, a, 5200);
  CHECK(s.route() == RouteKnowledge::Unknown);
}

static void anAnswerWithoutTheEventIsAFailedAttempt() {
  SenderPolicy s;
  TrackBuffer t;
  CHECK(s.takeLive("o1", ev("o1"), EventTier::Ok, 0, 0) == TakeResult::Queued);
  PreparedReport rep;
  CHECK(prepareReport(s, t, 0, rep));
  ReportResult r = finishReport(s, t, rep, false, answer(200, POINTS_OK, true), 100);
  CHECK(std::string(r.result) == "failed" && r.detail == "bad_response");
  CHECK(s.livePending());
  CHECK(s.route() == RouteKnowledge::Active);  // the route part was still readable
}

int main() {
  RUN(nothingDueMeansNoAttempt);
  RUN(aPointsOnlyReportCarriesEveryPoint);
  RUN(aLivenessReportHasNoPoints);
  RUN(aFailedReportDropsItsPoints);
  RUN(aPreemptedReportPutsItsPointsBack);
  RUN(aDeliveredEventIsAckedToThePi);
  RUN(aRejectedEventIsFailedWithTheBackendsReason);
  RUN(aFailedEventRetriesAndCountsItsAttempts);
  RUN(aDlqReplayIsLoggedAsTheNthOfFive);
  RUN(unauthorizedForgetsTheRoute);
  RUN(anAnswerWithoutTheEventIsAFailedAttempt);
  return report("sender_step_host_test");
}
