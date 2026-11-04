#include "cabin_signals.h"

SmsQueueResult cabinCameraSms(const AlarmPolicy& alarm, SmsPolicy& sms,
                              const EventPosition& where, bool hasPhone, uint32_t nowMs) {
  return sms.onCameraCritical(alarm.visionEpisode().str(), alarm.visionAlertType().str(),
                              alarm.gripAlarm(), where, hasPhone, nowMs);
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
  // The camera's latest reading first (the payload's own reason: a "No Face" class record before
  // the 5 s critical has none, and must not count as no_face).
  alarm.onCameraReading(dlqSession, record.severity, record.reason, nowMs);
  s.vision = alarm.onPiRecord(decision.kind, dlqSession, taken, episode, alertType, nowMs);
  // Every critical texts: the vision alarm turning on is exactly "a new live critical episode".
  if (s.vision == VisionChange::On) s.sms = cabinCameraSms(alarm, sms, where, hasPhone, nowMs);
  return s;
}
