#include "cabin_signals.h"

SmsQueueResult cabinAlarmSms(const AlarmPolicy& alarm, SmsPolicy& sms, const EventPosition& where,
                             bool hasPhone, uint32_t nowMs) {
  if (!alarm.buzzer()) return SmsQueueResult();  // Ignored: the double check isn't met
  return sms.onBothAlarms(alarm.visionEpisode().str(), alarm.visionAlertType().str(), where,
                          hasPhone, nowMs);
}

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
  // Only a vision alarm that just turned on can complete the pair here; the grip side calls
  // cabinAlarmSms() itself when its episode starts.
  if (s.vision == VisionChange::On) s.sms = cabinAlarmSms(alarm, sms, where, hasPhone, nowMs);
  return s;
}
