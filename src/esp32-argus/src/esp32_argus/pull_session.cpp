#include "pull_session.h"

namespace {

RecordDecision fromTake(TakeResult take, PiEventKind kind) {
  RecordDecision d;
  d.kind = kind;
  d.take = take;
  // AlreadyHeld: TAKEN again (it was just served) and keep the one copy (C1.8 [sync 1]).
  d.action = take == TakeResult::Queued || take == TakeResult::AlreadyHeld ? RecordAction::Take
                                                                            : RecordAction::Leave;
  return d;
}

}  // namespace

RecordDecision decidePulledRecord(ParseOutcome parsed, const PiRecord& record, bool dlqSession,
                                  SenderPolicy& sender, VigilanceTracker& vigilance,
                                  const HerePosition& here, uint32_t nowMs) {
  RecordDecision d;
  if (parsed == ParseOutcome::Drop) {
    // Never usable here; ACK it away when the id is known so it can't block the Pi's queue.
    d.action = record.id.empty() ? RecordAction::Leave : RecordAction::Ack;
    return d;
  }
  if (!dlqSession) vigilance.onPiRecord(parsePiSeverity(record.severity), nowMs);

  d.kind = classifyPiEvent(record);
  if (d.kind == PiEventKind::None) {
    d.action = RecordAction::Ack;  // used for vigilance only
    return d;
  }
  if (!record.hasAgeS) {
    d.action = RecordAction::FailTooOld;  // the Pi archives it
    return d;
  }
  if (!recordFitsEvent(record)) {
    d.action = RecordAction::FailInvalid;  // the backend would reject it; the Pi archives it
    return d;
  }

  if (!dlqSession) {
    ReportEvent ev = eventFromRecord(record, d.kind, here.position, here.hasSpeed, here.speedKmh);
    return fromTake(sender.takeLive(record.id, ev, tierFor(d.kind), record.ageS, nowMs), d.kind);
  }

  // A dead letter: the event as composed when it first failed (its position at the event), or,
  // with none stored (esp32_unreachable), composed now from the record with no position: this
  // device never saw where it happened.
  ReportEvent ev;
  if (!eventFromStoredJson(record.dlqBody, ev)) {
    EventPosition where;  // unknown, unless a v1 firmware's body says where it happened
    bool hasSpeed = false;
    float speed = 0.0f;
    legacyPositionFromBody(record.dlqBody, where, hasSpeed, speed);
    ev = eventFromRecord(record, d.kind, where, hasSpeed, speed);
  }
  ev.clientEventId = record.id;  // always the row's id: the Pi's ACK/FAIL and backend dedup key
  return fromTake(
      sender.takeDlq(record.id, ev, record.ageS, record.dlqQueuedS, record.dlqAttempts, nowMs),
      d.kind);
}

void addRecordDecision(SessionReport& report, const std::string& id, const RecordDecision& d) {
  switch (d.action) {
    case RecordAction::Ack: report.acks.push_back(id); break;
    case RecordAction::Take: report.taken.push_back(id); break;
    case RecordAction::FailTooOld: report.fails.push_back({id, "too_old", ""}); break;
    case RecordAction::FailInvalid: report.fails.push_back({id, "invalid", ""}); break;
    case RecordAction::Leave:
      if (d.kind != PiEventKind::None && d.take == TakeResult::Full) {
        report.leftFull++;
      } else {
        report.leftOther++;
      }
      break;
  }
}

void addPendingOutcomes(SessionReport& report, const SenderPolicy& sender) {
  for (int i = 0; i < sender.outcomeCount(); i++) {
    const PiOutcome& o = sender.outcome(i);
    if (o.ack) {
      report.acks.push_back(o.recordId.str());
    } else {
      report.fails.push_back({o.recordId.str(), o.reason.str(), o.body.str()});
    }
  }
  report.outcomesIncluded = sender.outcomeCount();
  report.outcomeSeqEnd = sender.outcomeSeqEnd();
}
