#pragma once

// What one record from the Pi means for the backend, decided from the Pi's own severity (the
// parts rules: none/low/medium/critical). There's no grip sensor yet, so the camera's verdict
// is the alert: medium -> a medium alert, critical -> a critical one, right away (the Pi's rules
// already waited 2-3 s of closed eyes). fusion.* (camera + grip) stays for when the sensor is
// wired. Pure logic with std::string, so test/host/pi_event_policy_host_test.cpp runs it on the
// PC.

#include <string>

#include "pi_record.h"

enum class PiSeverity : unsigned char { Unknown, None, Low, Medium, Critical };

PiSeverity parsePiSeverity(const std::string& text);
const char* piSeverityName(PiSeverity s);
// The backend's Severity for Status_Route.vigilance / Alert.severity_level: none and low are
// both "low" (nothing to act on), medium and critical map to themselves.
const char* backendSeverity(PiSeverity s);
bool isAlerting(PiSeverity s);  // medium or critical

enum class EventAction : unsigned char {
  Nothing,       // a keepalive, or a change that needs no backend call (none <-> low)
  RaiseAlert,    // the severity rose to medium/critical
  ResolveAlert,  // it fell back to none/low after an alert
  PostStatus,    // a heartbeat: the truck's position + current vigilance
};

const char* eventActionName(EventAction a);

// The backend alert id raised for each open episode (the Pi's payload.episode: one id from the
// moment the severity reaches medium until it falls back), so an escalation can link to it and
// a recovery can resolve it. Small and bounded: the oldest is forgotten first.
class EpisodeAlerts {
 public:
  void remember(const std::string& episode, const std::string& alertId);
  bool lookup(const std::string& episode, std::string& alertId) const;
  void forget(const std::string& episode);

 private:
  static const int kSlots = 8;
  std::string episodes_[kSlots];
  std::string alertIds_[kSlots];
  int next_ = 0;
};

struct EventDecision {
  EventAction action = EventAction::Nothing;
  PiSeverity severity = PiSeverity::Unknown;
  // RaiseAlert: set when this episode already has an alert (an escalation, e.g. medium ->
  // critical): the backend links the new alert to it (related_alert_id).
  std::string relatedAlertId;
  // ResolveAlert: the alert to resolve; empty if this firmware never raised one for the
  // episode (it rebooted, or the alert is still in the Pi's dead-letter queue).
  std::string resolveAlertId;
};

EventDecision decidePiEvent(const PiRecord& record, const EpisodeAlerts& episodes);
