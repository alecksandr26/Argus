#include "cabin_signals.h"

CabinSignals cabinSignalsFor(const PiRecord& record, ParseOutcome parsed,
                             const RecordDecision& decision, bool dlqSession,
                             const EventPosition& where, bool hasPhone, AlarmPolicy& alarm,
                             SmsPolicy& sms, uint32_t nowMs) {
  CabinSignals s;
  if (parsed != ParseOutcome::Ok) return s;
  // TAKEN covers a row served again while still held (AlreadyHeld): its episode dedupes it.
  bool taken = decision.action == RecordAction::Take;
  // The episode and alert type exactly as the backend event has them.
  const std::string& episode = eventEpisode(record);
  const std::string& alertType = eventAlertType(record);
  s.vision = alarm.onPiRecord(decision.kind, dlqSession, taken, episode, alertType, nowMs);
  s.sms = sms.onPiRecord(decision.kind, dlqSession, taken, episode, alertType, where, hasPhone,
                         nowMs);
  return s;
}
