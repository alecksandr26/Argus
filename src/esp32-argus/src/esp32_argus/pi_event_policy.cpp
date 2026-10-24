#include "pi_event_policy.h"

PiSeverity parsePiSeverity(const std::string& text) {
  if (text == "none") return PiSeverity::None;
  if (text == "low") return PiSeverity::Low;
  if (text == "medium") return PiSeverity::Medium;
  if (text == "critical") return PiSeverity::Critical;
  return PiSeverity::Unknown;
}

const char* piSeverityName(PiSeverity s) {
  switch (s) {
    case PiSeverity::None: return "none";
    case PiSeverity::Low: return "low";
    case PiSeverity::Medium: return "medium";
    case PiSeverity::Critical: return "critical";
    case PiSeverity::Unknown: break;
  }
  return "?";
}

const char* backendVigilance(PiSeverity s) {
  switch (s) {
    case PiSeverity::None:
    case PiSeverity::Low: return "low";
    case PiSeverity::Medium: return "medium";
    case PiSeverity::Critical: return "critical";
    case PiSeverity::Unknown: break;
  }
  return "unknown";
}

bool isAlerting(PiSeverity s) { return s == PiSeverity::Medium || s == PiSeverity::Critical; }

const char* piEventKindName(PiEventKind k) {
  switch (k) {
    case PiEventKind::None: return "no event";
    case PiEventKind::AlertMedium: return "alert medium";
    case PiEventKind::AlertCritical: return "alert critical";
    case PiEventKind::DriverOk: return "driver OK";
  }
  return "?";
}

namespace {
// rank(none)=0, rank(low)=1, rank(medium)=2, rank(critical)=3 (C1.5).
int rank(PiSeverity s) {
  switch (s) {
    case PiSeverity::Low: return 1;
    case PiSeverity::Medium: return 2;
    case PiSeverity::Critical: return 3;
    default: return 0;  // none, and a missing/unknown previous_severity ("treat as none")
  }
}
}  // namespace

PiEventKind classifyPiEvent(const PiRecord& record) {
  if (!record.transition) return PiEventKind::None;
  PiSeverity sev = parsePiSeverity(record.severity);
  if (sev == PiSeverity::Unknown) return PiEventKind::None;
  int prev = rank(parsePiSeverity(record.previousSeverity));
  if (sev == PiSeverity::Medium && prev < 2) return PiEventKind::AlertMedium;
  if (sev == PiSeverity::Critical && prev < 3) return PiEventKind::AlertCritical;
  if (rank(sev) <= 1 && prev >= 2) return PiEventKind::DriverOk;
  return PiEventKind::None;
}
