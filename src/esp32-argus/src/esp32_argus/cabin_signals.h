#pragma once

// The hook between the cabin's two inputs and its alarm (docs/designs/grip-alarm-contracts.md
// C4, C5, changed 2026-10-31). A pulled Pi record, once pull_session.h has classified it and
// TAKEN it (or not), moves the vision alarm (alarm_policy.h): only a live, taken critical turns it
// on, one per episode, a driver OK turns it off. A grip episode moves the grip alarm. The buzzer
// sounds and the one SMS is queued only when both are on: cabinAlarmSms() is called on either
// rising edge, so whichever comes second queues it. Pure, so test/host/cabin_signals_host_test.cpp
// runs real record lines through parsePiRecord() -> decidePulledRecord() -> here.

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
};

// The SMS for "both alarms on" (sms_policy.h onBothAlarms), or Ignored while only one is. Call
// it right after either alarm turned on, under the lock that guards both policies.
SmsQueueResult cabinAlarmSms(const AlarmPolicy& alarm, SmsPolicy& sms, const EventPosition& where,
                             bool hasPhone, uint32_t nowMs);

// Right after decidePulledRecord(), under the same lock. `dlqSession`: the record came from
// `DLQ 1`. `where`: the position composed into its event (the HerePosition the decision used).
// `hasPhone`: an SMS alert phone is set (credentials.h). An unusable record changes nothing.
CabinSignals cabinSignalsFor(const PiRecord& record, ParseOutcome parsed,
                             const RecordDecision& decision, bool dlqSession,
                             const EventPosition& where, bool hasPhone, AlarmPolicy& alarm,
                             SmsPolicy& sms, uint32_t nowMs);
