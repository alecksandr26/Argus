#pragma once

#include <Arduino.h>

#include "config.h"

// 1:1 firmware port of src/cv-argus/src/orchestrator/fusion_contract.py's
// FusionOrchestrator -- translate that file's logic here rather than re-deriving the
// state machine from the matrix/windows alone. See src/esp32-argus/README.md section 5
// for the full spec this implements.
//
// FusionReading::at is monotonic seconds (the caller derives it from millis()), matching
// the Python reference's documented convention. There's no thread here either -- update()
// is a plain call this device's main loop makes once per new FusionReading.

enum class GripState : uint8_t { GOOD, BAD };
enum class DrowsyState : uint8_t { NOT_DROWSY, DROWSY };

// Must match backend-argus's app/models/common.py Severity exactly -- hand-synced, no
// automated check, the same verbatim-duplication pattern fusion_contract.py's own
// Severity already uses.
enum class Severity : uint8_t { Low, Medium, Critical };

const char* severityToString(Severity s);

// Confirmed directly with the user, not guessed: both signals bad -> critical, both good
// -> low, either one alone bad -> medium.
Severity baseMatrixSeverity(DrowsyState drowsy, GripState grip);

struct FusionConfig {
  float debounceSeconds = FUSION_DEBOUNCE_SECONDS;
  float escalationSeconds = FUSION_ESCALATION_SECONDS;
  float recoverySeconds = FUSION_RECOVERY_SECONDS;
};

struct FusionReading {
  DrowsyState drowsy;
  GripState grip;
  float at; // monotonic seconds
};

// The subset of backend-argus's AlertCreate this engine decides on. id_route/coordinates/
// speed_at_event/timestamp are filled in by the HTTP relay layer (backend_api.*), not
// here -- see src/esp32-argus/README.md section 2.
struct AlertToRaise {
  bool present = false; // false == nothing new to post this tick
  Severity severityLevel;
  GripState gripStatus;
  bool isEscalation = false;  // true -> set related_alert_id to the open incident's id
  String relatedAlertId;      // only meaningful if isEscalation
};

// What update() tells the caller to do this tick.
struct FusionDecision {
  Severity severity; // the officially confirmed severity as of this tick -- see update()
  AlertToRaise alert;
  bool hasResolvedIncident = false;
  String resolvedIncidentId;
};

class FusionOrchestrator {
 public:
  explicit FusionOrchestrator(const FusionConfig& config = FusionConfig());

  // Feed one new reading; returns this tick's decision. See fusion_contract.py's
  // update() docstring for the full worse/unchanged/better branch reasoning -- ported
  // verbatim into handleWorsening/handleSteady/handleImproving below.
  FusionDecision update(const FusionReading& reading);

  // Call once, immediately after successfully POSTing an AlertToRaise this class
  // returned. No-op if no incident is currently open (a benign race, not caller error).
  void recordAlertPosted(const String& alertId);

  // The severity of the most recently posted alert for the currently open incident (LOW
  // if none is open) -- what Status_Route.vigilance should mirror, per
  // src/esp32-argus/README.md section 2's StatusRouteCreate field-mapping table.
  Severity confirmedSeverity() const { return _confirmedSeverity; }

 private:
  FusionDecision handleWorsening(const FusionReading& reading, Severity instantaneous);
  FusionDecision handleSteady(const FusionReading& reading);
  FusionDecision handleImproving(const FusionReading& reading, Severity instantaneous);

  FusionConfig _config;

  Severity _confirmedSeverity = Severity::Low;
  bool _hasOpenIncident = false;
  String _openIncidentId;
  bool _hasIncidentConfirmedAt = false;
  float _incidentConfirmedAt = 0.0f;

  bool _hasPendingSeverity = false;
  Severity _pendingSeverity;
  float _pendingSince = 0.0f;

  bool _hasRecoveringSince = false;
  float _recoveringSince = 0.0f;
};
