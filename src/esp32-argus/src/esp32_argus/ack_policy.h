#pragma once

// What one report attempt's answer means (docs/designs/final-tweaks-contracts.md C2.3): whether
// its event was delivered (ACK it to the Pi), rejected for good (FAIL <reason>, the Pi archives a
// critical / drops a medium or OK), or the attempt failed (the retry rules apply); what the
// backend now says about this truck's active route; and whether to re-fetch CONFIG. Points are
// done after every attempt either way: stored, refused, or dropped with a failed report (user
// decision). Pure logic, so test/host/ack_policy_host_test.cpp runs it on the PC.

#include <string>

enum class EventOutcome : unsigned char {
  NoEvent,        // the report carried no event
  Delivered,      // 200, event created or duplicate -> ACK <id>
  Rejected,       // 200 event rejected, or 413/422 (`invalid`) -> FAIL <id> <reason> -
  AttemptFailed,  // 401/429/5xx/other, transport error, unreadable 200 -> retry rules
};

struct ReportReaction {
  EventOutcome event = EventOutcome::NoEvent;
  // Rejected: the backend's reason (route_finished, no_route, too_old, wrong_truck, invalid; a
  // malformed one becomes `invalid`). AttemptFailed: http_<status>, bad_response, or the
  // transport reason the caller passed (no_network, no_credentials, timeout, transport_error).
  std::string reason;
  bool routeKnown = false;  // a readable 200: the active route below is the backend's answer
  std::string activeRoute;  // "" = null: no active route (points-only reports slow down)
  bool recredential = false;  // 401: the key was rotated or revoked -> ask the Pi for CONFIG
};

// `httpStatus` <= 0 means nothing reached the backend; `transportReason` says why.
ReportReaction reactToReport(int httpStatus, const std::string& body, bool sentEvent,
                             const char* transportReason);

const char* eventOutcomeName(EventOutcome outcome);
