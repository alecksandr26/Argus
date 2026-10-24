#pragma once

// The hook between a pulled Pi record and the cabin (docs/designs/grip-alarm-contracts.md C4,
// C5): once pull_session.h has classified the record and TAKEN it (or not), the vision alarm
// (alarm_policy.h) and the SMS queue (sms_policy.h) each apply their own rules to it -- only a
// live, taken critical starts anything, no_face never sounds, one of each per episode, a driver
// OK stops the vision alarm. Pure, so test/host/cabin_signals_host_test.cpp runs real record
// lines through parsePiRecord() -> decidePulledRecord() -> here.

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

// Right after decidePulledRecord(), under the same lock. `dlqSession`: the record came from
// `DLQ 1`. `where`: the position composed into its event (the HerePosition the decision used).
// `hasPhone`: an SMS alert phone is set (credentials.h). An unusable record changes nothing.
CabinSignals cabinSignalsFor(const PiRecord& record, ParseOutcome parsed,
                             const RecordDecision& decision, bool dlqSession,
                             const EventPosition& where, bool hasPhone, AlarmPolicy& alarm,
                             SmsPolicy& sms, uint32_t nowMs);
