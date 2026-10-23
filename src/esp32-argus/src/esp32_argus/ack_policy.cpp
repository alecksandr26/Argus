#include "ack_policy.h"

#include "backend_messages.h"
#include "pi_protocol.h"

namespace {

ReportReaction failed(bool sentEvent, const std::string& reason) {
  ReportReaction r;
  r.event = sentEvent ? EventOutcome::AttemptFailed : EventOutcome::NoEvent;
  r.reason = reason;
  return r;
}

}  // namespace

ReportReaction reactToReport(int httpStatus, const std::string& body, bool sentEvent,
                             const char* transportReason) {
  if (httpStatus <= 0) {
    std::string why = transportReason && *transportReason ? transportReason : "transport_error";
    return failed(sentEvent, reasonToken(why));
  }
  if (httpStatus == 401) {
    ReportReaction r = failed(sentEvent, "http_401");
    r.recredential = true;
    return r;
  }
  if (httpStatus == 413 || httpStatus == 422) {
    // A firmware bug: retrying the same body can't succeed. Points dropped; the event is
    // treated as rejected invalid.
    ReportReaction r;
    r.event = sentEvent ? EventOutcome::Rejected : EventOutcome::NoEvent;
    r.reason = "invalid";
    return r;
  }
  if (httpStatus < 200 || httpStatus >= 300) {
    return failed(sentEvent, "http_" + std::to_string(httpStatus));  // 429, 5xx, other 4xx
  }

  ReportResponse resp;
  if (!parseReportResponse(body, resp)) return failed(sentEvent, "bad_response");

  ReportReaction r;
  r.routeKnown = true;
  r.activeRoute = resp.hasActiveRoute ? resp.activeRoute : "";
  if (!sentEvent) return r;
  switch (resp.eventStatus) {
    case EventStatus::Created:
    case EventStatus::Duplicate:
      r.event = EventOutcome::Delivered;
      break;
    case EventStatus::Rejected:
      r.event = EventOutcome::Rejected;
      r.reason = isReasonToken(resp.eventReason) ? resp.eventReason : "invalid";
      break;
    case EventStatus::Absent:
    case EventStatus::Unknown:
      // A 200 that doesn't say what happened to the event: try again (a duplicate is harmless).
      r.event = EventOutcome::AttemptFailed;
      r.reason = "bad_response";
      break;
  }
  return r;
}

const char* eventOutcomeName(EventOutcome outcome) {
  switch (outcome) {
    case EventOutcome::NoEvent: return "no event";
    case EventOutcome::Delivered: return "delivered";
    case EventOutcome::Rejected: return "rejected";
    case EventOutcome::AttemptFailed: return "failed";
  }
  return "?";
}
