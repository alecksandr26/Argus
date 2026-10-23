// What a report attempt's answer means (docs/designs/final-tweaks-contracts.md C2.3). The old
// per-request relay policy (201 ack, 409 drop, 404 refetch) is gone with the legacy endpoints;
// the same statuses are covered here under the report endpoint's rules.
// SOURCES: ack_policy.cpp backend_messages.cpp pi_event_policy.cpp pi_protocol.cpp
// NEEDS: arduinojson
#include <string>

#include "ack_policy.h"
#include "check.h"

static const char* CREATED =
    R"({"points":{"stored":5,"route":"r1","status":"ok"},)"
    R"("event":{"status":"created","reason":null,"id_alert":"a1"},"active_route":"r1"})";

static void createdAndDuplicateAreDelivered() {
  ReportReaction r = reactToReport(200, CREATED, true, nullptr);
  CHECK(r.event == EventOutcome::Delivered);
  CHECK(r.routeKnown);
  CHECK(r.activeRoute == "r1");
  CHECK(!r.recredential);
  r = reactToReport(200,
                    R"({"points":{"stored":0,"status":"ok"},"event":{"status":"duplicate",)"
                    R"("id_alert":"a1"},"active_route":"r1"})",
                    true, nullptr);
  CHECK(r.event == EventOutcome::Delivered);
}

static void rejectionsStopRetryingWithTheBackendsReason() {
  for (const char* reason : {"route_finished", "no_route", "too_old", "wrong_truck", "invalid"}) {
    std::string body = std::string(R"({"points":{"stored":0,"status":"route_finished"},)") +
                       R"("event":{"status":"rejected","reason":")" + reason +
                       R"(","id_alert":null},"active_route":null})";
    ReportReaction r = reactToReport(200, body, true, nullptr);
    CHECK(r.event == EventOutcome::Rejected);
    CHECK(r.reason == reason);
    CHECK(r.routeKnown);
    CHECK(r.activeRoute.empty());
  }
}

static void aMalformedRejectionReasonBecomesInvalid() {
  ReportReaction r = reactToReport(
      200, R"({"points":{},"event":{"status":"rejected","reason":"two words"},"active_route":null})",
      true, nullptr);
  CHECK(r.event == EventOutcome::Rejected);
  CHECK(r.reason == "invalid");
}

static void anUnknownButWellFormedReasonBecomesInvalid() {
  // C1.8 [sync 2]: a reason outside C2.2's five would make the Pi count a retryable attempt
  // (C1.6 rule 4) for an event the backend refused for good.
  for (const char* reason : {"route_gone", "http_503", "preempted", "retry_window", ""}) {
    std::string body = std::string(R"({"points":{},"event":{"status":"rejected","reason":")") +
                       reason + R"("},"active_route":null})";
    ReportReaction r = reactToReport(200, body, true, nullptr);
    CHECK(r.event == EventOutcome::Rejected);
    CHECK(r.reason == "invalid");
  }
  ReportReaction r = reactToReport(
      200, R"({"points":{},"event":{"status":"rejected","reason":null},"active_route":null})",
      true, nullptr);
  CHECK(r.reason == "invalid");
}

static void pointsOnlyReportsJustLearnTheRoute() {
  ReportReaction r = reactToReport(
      200, R"({"points":{"stored":0,"route":null,"status":"no_active_route"},"event":null,"active_route":null})",
      false, nullptr);
  CHECK(r.event == EventOutcome::NoEvent);
  CHECK(r.routeKnown);
  CHECK(r.activeRoute.empty());
}

static void aTwoHundredThatForgetsTheEventIsAFailedAttempt() {
  ReportReaction r = reactToReport(
      200, R"({"points":{"stored":1,"status":"ok"},"event":null,"active_route":"r1"})", true,
      nullptr);
  CHECK(r.event == EventOutcome::AttemptFailed);
  CHECK(r.reason == "bad_response");
  CHECK(r.routeKnown);  // the route part is still the backend's answer
}

static void unreadableTwoHundredIsAFailedAttempt() {
  ReportReaction r = reactToReport(200, "<html>oops</html>", true, nullptr);
  CHECK(r.event == EventOutcome::AttemptFailed);
  CHECK(r.reason == "bad_response");
  CHECK(!r.routeKnown);
}

static void unauthorizedRefetchesConfig() {
  ReportReaction r = reactToReport(401, R"({"detail":"x"})", true, nullptr);
  CHECK(r.event == EventOutcome::AttemptFailed);
  CHECK(r.reason == "http_401");
  CHECK(r.recredential);
  CHECK(!r.routeKnown);
  CHECK(reactToReport(401, "", false, nullptr).recredential);
}

static void firmwareBugsAreRejectedInvalid() {
  CHECK(reactToReport(413, "", true, nullptr).event == EventOutcome::Rejected);
  ReportReaction r = reactToReport(422, R"({"detail":[]})", true, nullptr);
  CHECK(r.event == EventOutcome::Rejected);
  CHECK(r.reason == "invalid");
  CHECK(reactToReport(422, "", false, nullptr).event == EventOutcome::NoEvent);
}

static void serverErrorsAreFailedAttempts() {
  CHECK(reactToReport(503, "", true, nullptr).reason == "http_503");
  CHECK(reactToReport(500, "", true, nullptr).event == EventOutcome::AttemptFailed);
  CHECK(reactToReport(429, "", true, nullptr).reason == "http_429");
  CHECK(reactToReport(404, "", true, nullptr).reason == "http_404");
  CHECK(reactToReport(403, "", true, nullptr).event == EventOutcome::AttemptFailed);
  CHECK(reactToReport(502, "", false, nullptr).event == EventOutcome::NoEvent);
}

static void transportErrorsCarryTheCallersReason() {
  CHECK(reactToReport(-1, "", true, "no_network").reason == "no_network");
  CHECK(reactToReport(-1, "", true, "timeout").reason == "timeout");
  CHECK(reactToReport(0, "", true, "no_credentials").reason == "no_credentials");
  CHECK(reactToReport(-11, "", true, nullptr).reason == "transport_error");
  CHECK(reactToReport(-1, "", true, "").reason == "transport_error");
  CHECK(reactToReport(-1, "", true, "no_network").event == EventOutcome::AttemptFailed);
  // The SIM800L still busy with an earlier timed-out request: a failed attempt (retry rules), its
  // own token, so the Pi's dlq_error and the [live]/[dlq] line say what happened.
  ReportReaction busy = reactToReport(-1, "", true, "modem_busy");
  CHECK(busy.event == EventOutcome::AttemptFailed && busy.reason == "modem_busy");
  CHECK(!busy.recredential && !busy.routeKnown);
}

int main() {
  RUN(createdAndDuplicateAreDelivered);
  RUN(rejectionsStopRetryingWithTheBackendsReason);
  RUN(aMalformedRejectionReasonBecomesInvalid);
  RUN(anUnknownButWellFormedReasonBecomesInvalid);
  RUN(pointsOnlyReportsJustLearnTheRoute);
  RUN(aTwoHundredThatForgetsTheEventIsAFailedAttempt);
  RUN(unreadableTwoHundredIsAFailedAttempt);
  RUN(unauthorizedRefetchesConfig);
  RUN(firmwareBugsAreRejectedInvalid);
  RUN(serverErrorsAreFailedAttempts);
  RUN(transportErrorsCarryTheCallersReason);
  return report("ack_policy_host_test");
}
