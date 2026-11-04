#pragma once

// The hook between a pulled Pi record and the cabin (docs/designs/grip-alarm-contracts.md C4, C5,
// changed 2026-11-03). Once pull_session.h has classified a record and TAKEN it (or not):
// - every usable live record, keepalives included, gives the alarm the camera's latest reading
//   (alarm_policy.h onCameraReading: the no-face cap follows it);
// - a live, taken critical turns the vision alarm on (one per episode) and queues that episode's
//   SMS at once, hands on the wheel or not (every critical texts); a driver OK turns it off.
// The grip side (the sketch's onGripChange) calls AlarmPolicy::setGripAlarm() and
// SmsPolicy::onGripCritical() itself. Pure, so test/host/cabin_signals_host_test.cpp runs real
// record lines through parsePiRecord() -> decidePulledRecord() -> here.

#include <cstdint>

#include "alarm_policy.h"
#include "backend_messages.h"
#include "pi_record.h"
#include "pull_session.h"
#include "sms_policy.h"

// What one record did, for its log lines.
struct CabinSignals {
  VisionChange vision = VisionChange::None;
  SmsQueueResult sms;
  EventPosition where;  // the position composed into the record's event (for the log lines)
};

// The SMS for the camera critical the vision alarm just turned on for (sms_policy.h
// onCameraCritical), saying whether the hands are off right now. Under the lock that guards both
// policies.
SmsQueueResult cabinCameraSms(const AlarmPolicy& alarm, SmsPolicy& sms, const EventPosition& where,
                              bool hasPhone, uint32_t nowMs);

// Right after decidePulledRecord(), under the same lock. `dlqSession`: the record came from
// `DLQ 1`. `where`: the position composed into its event (the HerePosition the decision used).
// `hasPhone`: an SMS alert phone is set (credentials.h). An unusable record changes nothing.
CabinSignals cabinSignalsFor(const PiRecord& record, ParseOutcome parsed,
                             const RecordDecision& decision, bool dlqSession,
                             const EventPosition& where, bool hasPhone, AlarmPolicy& alarm,
                             SmsPolicy& sms, uint32_t nowMs);
