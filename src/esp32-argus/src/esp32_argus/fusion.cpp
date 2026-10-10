#include "fusion.h"

namespace {
int severityRank(Severity s) {
  switch (s) {
    case Severity::Low: return 0;
    case Severity::Medium: return 1;
    case Severity::Critical: return 2;
  }
  return 0;
}
}  // namespace

const char* severityToString(Severity s) {
  switch (s) {
    case Severity::Low: return "low";
    case Severity::Medium: return "medium";
    case Severity::Critical: return "critical";
  }
  return "low";
}

Severity baseMatrixSeverity(DrowsyState drowsy, GripState grip) {
  if (drowsy == DrowsyState::NOT_DROWSY && grip == GripState::GOOD) return Severity::Low;
  if (drowsy == DrowsyState::NOT_DROWSY && grip == GripState::BAD) return Severity::Medium;
  if (drowsy == DrowsyState::DROWSY && grip == GripState::GOOD) return Severity::Medium;
  return Severity::Critical;  // DROWSY + BAD
}

FusionOrchestrator::FusionOrchestrator(const FusionConfig& config) : _config(config) {}

FusionDecision FusionOrchestrator::update(const FusionReading& reading) {
  Severity instantaneous = baseMatrixSeverity(reading.drowsy, reading.grip);
  int confirmedRank = severityRank(_confirmedSeverity);
  int instantaneousRank = severityRank(instantaneous);

  if (instantaneousRank > confirmedRank) return handleWorsening(reading, instantaneous);
  if (instantaneousRank == confirmedRank) return handleSteady(reading);
  return handleImproving(reading, instantaneous);
}

FusionDecision FusionOrchestrator::handleWorsening(const FusionReading& reading,
                                                     Severity instantaneous) {
  _hasRecoveringSince = false;  // this reading isn't fully good

  if (!_hasPendingSeverity || _pendingSeverity != instantaneous) {
    _hasPendingSeverity = true;
    _pendingSeverity = instantaneous;
    _pendingSince = reading.at;
  }

  FusionDecision decision;
  float elapsed = reading.at - _pendingSince;
  if (elapsed < _config.debounceSeconds) {
    decision.severity = _confirmedSeverity;
    return decision;
  }

  // Debounce satisfied -- confirm it. An incident was already open iff we weren't at LOW,
  // which makes this an escalation (checked *before* overwriting _confirmedSeverity).
  bool isEscalation = (_confirmedSeverity != Severity::Low);

  decision.alert.present = true;
  decision.alert.severityLevel = instantaneous;
  decision.alert.gripStatus = reading.grip;
  decision.alert.isEscalation = isEscalation;
  decision.alert.relatedAlertId = isEscalation ? _openIncidentId : String();

  _confirmedSeverity = instantaneous;
  _incidentConfirmedAt = reading.at;
  _hasIncidentConfirmedAt = true;
  _hasPendingSeverity = false;

  decision.severity = instantaneous;
  return decision;
}

FusionDecision FusionOrchestrator::handleSteady(const FusionReading& reading) {
  _hasPendingSeverity = false;
  _hasRecoveringSince = false;

  FusionDecision decision;
  decision.severity = _confirmedSeverity;

  if (_confirmedSeverity == Severity::Medium && _hasIncidentConfirmedAt) {
    float elapsed = reading.at - _incidentConfirmedAt;
    if (elapsed >= _config.escalationSeconds) {
      decision.alert.present = true;
      decision.alert.severityLevel = Severity::Critical;
      decision.alert.gripStatus = reading.grip;
      decision.alert.isEscalation = true;
      decision.alert.relatedAlertId = _openIncidentId;

      _confirmedSeverity = Severity::Critical;
      _incidentConfirmedAt = reading.at;

      decision.severity = Severity::Critical;
    }
  }

  // Steady LOW, steady MEDIUM below the escalation threshold, or steady CRITICAL (which
  // has nowhere further to escalate to -- intentional, not an oversight).
  return decision;
}

FusionDecision FusionOrchestrator::handleImproving(const FusionReading& reading,
                                                     Severity instantaneous) {
  _hasPendingSeverity = false;

  FusionDecision decision;
  decision.severity = _confirmedSeverity;

  if (instantaneous != Severity::Low) {
    // Partial improvement (e.g. critical -> medium). No de-escalation: severity stays
    // unchanged, and this reading isn't fully good, so no recovery progress either.
    _hasRecoveringSince = false;
    return decision;
  }

  // Fully good.
  if (!_hasRecoveringSince) {
    _hasRecoveringSince = true;
    _recoveringSince = reading.at;
  }

  float elapsed = reading.at - _recoveringSince;
  if (elapsed < _config.recoverySeconds) {
    return decision;
  }

  // Recovered -- resolve whatever the latest posted row was, and reset to a clean slate.
  if (_hasOpenIncident) {
    decision.hasResolvedIncident = true;
    decision.resolvedIncidentId = _openIncidentId;
  }
  _confirmedSeverity = Severity::Low;
  _hasOpenIncident = false;
  _openIncidentId = "";
  _hasIncidentConfirmedAt = false;
  _hasRecoveringSince = false;

  decision.severity = Severity::Low;
  return decision;
}

void FusionOrchestrator::recordAlertPosted(const String& alertId) {
  // Unconditionally overwrites the tracked id, which is exactly what lets one field serve
  // two roles: read as related_alert_id on a later escalation, and read as
  // resolvedIncidentId at recovery. No-op if no incident is open -- a benign race (e.g. a
  // slow POST completing after the driver already recovered), not caller error.
  if (_confirmedSeverity == Severity::Low) return;
  _openIncidentId = alertId;
  _hasOpenIncident = true;
}
