#pragma once

// What this device does with each record the Pi serves in a `PULL` or `DLQ 1` session, and the
// report lines that end the session (docs/designs/final-tweaks-contracts.md C1.3, C1.8). Pure,
// so test/host/pull_session_host_test.cpp runs it on the PC; bluetooth_client.cpp does the I/O.
//
// PULL (live): every readable record updates the vigilance; a non-event is ACKed; an event (C1.5)
// is composed now (this device's position) and handed to the sender (TAKEN), or FAILed too_old
// when the Pi can't tell its age. DLQ: the one dead letter is replayed from its stored event
// (or composed from the record when the Pi has none -- with no position, or the one in a v1
// firmware's leftover body), age and queued time carried forward. The session's report lines also carry every finished event's ACK/FAIL.

#include <string>
#include <vector>

#include "backend_messages.h"
#include "gps_track.h"
#include "pi_protocol.h"
#include "pi_record.h"
#include "sender_policy.h"

enum class RecordAction : uint8_t {
  Ack,         // nothing to deliver (a keepalive, a non-event, an unusable row with an id)
  Take,        // held by the sender: TAKEN
  FailTooOld,  // an event whose age the Pi can't tell: FAIL <id> too_old -
  Leave,       // not reported: stays on the Pi as it was (no id, sender full, finished already)
};

struct RecordDecision {
  RecordAction action = RecordAction::Leave;
  PiEventKind kind = PiEventKind::None;
  TakeResult take = TakeResult::Full;  // Take / Leave of an event: what the sender said
};

// Where the truck is right now, for an event composed live.
struct HerePosition {
  EventPosition position;
  bool hasSpeed = false;  // a current fix (speed is then meaningful)
  float speedKmh = 0.0f;
};

RecordDecision decidePulledRecord(ParseOutcome parsed, const PiRecord& record, bool dlqSession,
                                  SenderPolicy& sender, VigilanceTracker& vigilance,
                                  const HerePosition& here, uint32_t nowMs);

// The report lines collected over one session.
struct SessionReport {
  std::vector<std::string> acks;
  std::vector<FailReport> fails;
  std::vector<std::string> taken;
  int outcomesIncluded = 0;  // how many of the sender's outcomes these lines carry
};

void addRecordDecision(SessionReport& report, const std::string& id, const RecordDecision& d);
// Adds every finished event's ACK/FAIL (oldest first). Once the Pi answers OK, the caller drops
// `outcomesIncluded` of them from the sender (SenderPolicy::dropOutcomes).
void addPendingOutcomes(SessionReport& report, const SenderPolicy& sender);
