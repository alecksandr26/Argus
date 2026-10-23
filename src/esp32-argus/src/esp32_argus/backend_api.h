#pragma once

#include <Arduino.h>

#include <string>

// Sending to backend-argus. The firmware makes exactly one kind of request: POST
// /api/devices/me/report (docs/designs/final-tweaks-contracts.md C2), composed by the sender task
// (sender_step.h). The v1 calls are gone: POST /api/alerts, the route status POST, the resolve
// call and the GET /api/devices/me/active-route lookup (the report's answer carries
// `active_route`). Auth: X-Device-Api-Key, the key the Pi hands over via CONFIG (credentials.h).
//
// Transport: mobile data (SIM800L GPRS, gprs_http.h) first, WiFi as the fallback
// (transport_policy.h), one transport per attempt. Over WiFi an https:// backend is verified
// against the pinned root CA (root_ca.h). Every attempt has a real deadline from
// attemptTimeoutMs(). Only the sender task calls into this module.

struct ReportSend {
  int status = -1;          // the HTTP status, or <= 0 when nothing came back
  String body;              // the answer's body (a 200's JSON)
  const char* reason = "";  // no answer: no_credentials | no_network | timeout | transport_error
  bool preempted = false;   // stopped between AT steps for a higher event (nothing was sent)
  const char* via = "none"; // gprs | wifi | none
};

// One attempt. `retry`: an event's retry or a DLQ replay (the longer mobile timeout).
// `preempt` is asked between AT steps of a mobile attempt (and once before a WiFi one).
ReportSend backendPostReport(const std::string& body, bool retry, bool (*preempt)());

// Between attempts (the sender task's idle loop): WiFi upkeep and a late +HTTPACTION.
void backendPoll();
