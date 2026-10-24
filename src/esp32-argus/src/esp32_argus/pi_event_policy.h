#pragma once

// Which Pi records are backend events, decided from the Pi's own severity (the parts rules:
// none/low/medium/critical) with the exact rule both sides share
// (docs/designs/final-tweaks-contracts.md C1.5). One alert per incident: a rise to medium is a
// medium alert, a rise to critical (a raise or an escalation) a critical one, and the fall back
// to none/low after either is one "driver OK". Everything else (keepalives, none <-> low,
// critical -> medium, class-only changes) is not an event: it only feeds the GPS points'
// `vigilance`. There's no grip sensor yet, so fusion.* stays unused. Pure logic with
// std::string, so test/host/pi_event_policy_host_test.cpp runs it on the PC.

#include <string>

#include "pi_record.h"

enum class PiSeverity : unsigned char { Unknown, None, Low, Medium, Critical };

PiSeverity parsePiSeverity(const std::string& text);
const char* piSeverityName(PiSeverity s);
// The backend's vigilance for a known Pi severity: none and low are both "low" (nothing to act
// on), medium and critical map to themselves. Unknown maps to "unknown" (C2.1 points[]).
const char* backendVigilance(PiSeverity s);
bool isAlerting(PiSeverity s);  // medium or critical

enum class PiEventKind : unsigned char {
  None,           // not a backend event: ACK it in the same session
  AlertMedium,    // kind "alert", severity "medium"
  AlertCritical,  // kind "alert", severity "critical" (a raise or an escalation)
  DriverOk,       // kind "ok": back to none/low after a medium/critical
};

const char* piEventKindName(PiEventKind k);

// C1.5. Only `transition` records can be events; a missing previous_severity counts as "none";
// an unknown severity is never an event.
PiEventKind classifyPiEvent(const PiRecord& record);

// A critical event row (C1.5): the only kind the Pi's DLQ holds.
inline bool isCriticalEvent(PiEventKind k) { return k == PiEventKind::AlertCritical; }
