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

const char* backendSeverity(PiSeverity s) {
  switch (s) {
    case PiSeverity::Medium: return "medium";
    case PiSeverity::Critical: return "critical";
    default: return "low";
  }
}

bool isAlerting(PiSeverity s) { return s == PiSeverity::Medium || s == PiSeverity::Critical; }

const char* eventActionName(EventAction a) {
  switch (a) {
    case EventAction::Nothing: return "nothing to send";
    case EventAction::RaiseAlert: return "alert";
    case EventAction::ResolveAlert: return "resolve";
    case EventAction::PostStatus: return "status";
  }
  return "?";
}

void EpisodeAlerts::remember(const std::string& episode, const std::string& alertId) {
  if (episode.empty()) return;
  for (int i = 0; i < kSlots; i++) {
    if (episodes_[i] == episode) {
      alertIds_[i] = alertId;  // an escalation: resolve the newest alert of the episode
      return;
    }
  }
  episodes_[next_] = episode;
  alertIds_[next_] = alertId;
  next_ = (next_ + 1) % kSlots;
}

bool EpisodeAlerts::lookup(const std::string& episode, std::string& alertId) const {
  if (episode.empty()) return false;
  for (int i = 0; i < kSlots; i++) {
    if (episodes_[i] == episode) {
      alertId = alertIds_[i];
      return true;
    }
  }
  return false;
}

void EpisodeAlerts::forget(const std::string& episode) {
  for (int i = 0; i < kSlots; i++) {
    if (episodes_[i] == episode) {
      episodes_[i].clear();
      alertIds_[i].clear();
    }
  }
}

std::string EpisodeAlerts::serialize() const {
  std::string out = "EA1";
  for (int i = 0; i < kSlots; i++) {
    if (episodes_[i].empty()) continue;
    out += ";" + episodes_[i] + "=" + alertIds_[i];
  }
  return out;
}

bool EpisodeAlerts::deserialize(const std::string& blob) {
  *this = EpisodeAlerts();
  if (blob.compare(0, 3, "EA1") != 0) return false;
  size_t at = 3;
  while (at < blob.size()) {
    if (blob[at] != ';') {
      *this = EpisodeAlerts();
      return false;
    }
    size_t end = blob.find(';', at + 1);
    std::string item = blob.substr(at + 1, end == std::string::npos ? std::string::npos
                                                                     : end - at - 1);
    size_t eq = item.find('=');
    if (eq == std::string::npos || eq == 0) {
      *this = EpisodeAlerts();
      return false;
    }
    remember(item.substr(0, eq), item.substr(eq + 1));
    if (end == std::string::npos) break;
    at = end;
  }
  return true;
}

EventDecision decidePiEvent(const PiRecord& record, const EpisodeAlerts& episodes) {
  EventDecision d;
  if (record.kind == PiRecordKind::RouteStatus) {
    d.action = EventAction::PostStatus;
    return d;
  }
  d.severity = parsePiSeverity(record.severity);
  if (!record.transition || d.severity == PiSeverity::Unknown) return d;

  PiSeverity previous = parsePiSeverity(record.previousSeverity);
  std::string openAlert;
  bool hasOpenAlert = episodes.lookup(record.episode, openAlert);

  if (isAlerting(d.severity) && d.severity > previous) {
    d.action = EventAction::RaiseAlert;
    if (hasOpenAlert) d.relatedAlertId = openAlert;
  } else if (!isAlerting(d.severity) && isAlerting(previous)) {
    d.action = EventAction::ResolveAlert;
    if (hasOpenAlert) d.resolveAlertId = openAlert;
  }
  return d;
}
