#pragma once

// The critical-alert SMS (docs/designs/grip-alarm-contracts.md C5, changed 2026-11-03: every
// critical alert texts): which moments get one, the queue that waits for the GSM network, the
// retries, and the text. Pure logic with an injected millis() clock, so
// test/host/sms_policy_host_test.cpp runs it on the PC with exact strings.
//
// - A live camera critical (any reason, no_face included) the moment this device takes it,
//   whether or not the hands are on the wheel (onCameraCritical; the text adds "sin manos en el
//   volante" only when they are off then). Never for a medium, an OK or a DLQ replay.
// - A grip episode's start: the wheel's own no_grip critical (onGripCritical).
// - At most one per episode: a ring of the last SMS_EPISODE_MEMORY camera episodes dedupes a
//   record served again and the episode's later criticals; grip episodes have their own ring, so
//   a burst of them never makes a re-served camera critical text twice.
// - Only with an alert phone (CONFIG's alert_phone): without one the episode is skipped, logged
//   once (its result is NoPhone, then Duplicate).
// - A queue of SMS_QUEUE_SLOTS, deduped by episode; a new one when full drops the oldest waiting.
//   The sender task (the only modem owner) tries the oldest due one between report attempts, only
//   while the modem is registered on the GSM network: SMS_MAX_ATTEMPTS tries SMS_RETRY_GAP_MS
//   apart, and a message still unsent SMS_PENDING_MAX_MS after it was queued is dropped (logged).
//
// Not thread-safe by itself: the sketch keeps one instance behind its state mutex (the loop task
// and the grip task queue, the sender task sends).

#include <cstdint>
#include <string>

#include "backend_messages.h"
#include "bounded_string.h"
#include "config.h"
#include "episode_ring.h"
#include "grip_policy.h"  // kGripAlertType

struct SmsTiming {
  uint32_t retryGapMs = SMS_RETRY_GAP_MS;
  uint32_t pendingMaxMs = SMS_PENDING_MAX_MS;
  int maxAttempts = SMS_MAX_ATTEMPTS;
};

// One message waiting to go. A static slot: its text is fixed-capacity (bounded_string.h). The
// text itself is composed when it goes (composeSmsText), with the truck's plate as known then.
struct SmsMessage {
  bool used = false;
  IdString episode;
  AlertTypeString alertType;  // the camera critical's alert_type (the Pi's reason), or no_grip
  bool handsOff = false;      // the grip alarm was on when it was queued (the text says so)
  EventPosition position;     // where the truck was when the critical came
  uint32_t queuedAtMs = 0;
  int attempts = 0;           // failed tries so far
  uint32_t lastAttemptEndMs = 0;
};

enum class SmsQueued : uint8_t {
  Ignored,    // not a moment this rule sends an SMS for (a medium, an OK, a DLQ replay...)
  Queued,
  Duplicate,  // this episode already had its SMS (or was skipped): nothing new
  NoPhone,    // no alert phone set: skipped (log it: once per episode)
};

struct SmsQueueResult {
  SmsQueued result = SmsQueued::Ignored;
  bool evicted = false;  // the queue was full: the oldest waiting message was dropped for it
  IdString evictedEpisode;
  bool handsOff = false;    // Queued: the message's hands-off flag (for the log's text)
  uint32_t queuedAtMs = 0;  // Queued: when (the text's event time)
};

// The log's reason for an SMS that won't go (logSmsQueued in the sketch): NoPhone and Duplicate;
// nullptr for Queued and Ignored, which aren't skips.
const char* smsSkipReason(SmsQueued result);

enum class SmsAttemptEnd : uint8_t {
  Sent,     // +CMGS: and OK came back
  Retry,    // failed; tried again after SMS_RETRY_GAP_MS
  GaveUp,   // failed SMS_MAX_ATTEMPTS times: dropped
  Gone,     // no longer queued (dropped for a newer one while it was being sent)
};

class SmsPolicy {
 public:
  static constexpr int kSlots = SMS_QUEUE_SLOTS;
  static constexpr int kRemembered = SMS_EPISODE_MEMORY;

  explicit SmsPolicy(const SmsTiming& timing = SmsTiming());

  // A live camera critical was just taken (cabin_signals.h): `visionEpisode`, `alertType` (its
  // reason), `handsOff` the grip alarm is on now. `where`: the position then.
  SmsQueueResult onCameraCritical(const std::string& visionEpisode, const std::string& alertType,
                                  bool handsOff, const EventPosition& where, bool hasPhone,
                                  uint32_t nowMs);
  // A grip episode just started (its no_grip critical): `gripEpisode` its id (grip_policy.h).
  SmsQueueResult onGripCritical(const std::string& gripEpisode, const EventPosition& where,
                                bool hasPhone, uint32_t nowMs);

  // --- The sender task, between report attempts ---
  // Removes one message that waited longer than pendingMaxMs since it was queued into `dropped`;
  // call until false.
  bool expireOne(uint32_t nowMs, SmsMessage& dropped);
  // The oldest message to try now (never tried, or its retry gap is over); -1 = none.
  int due(uint32_t nowMs) const;
  bool pending() const;
  const SmsMessage& slot(int i) const { return slots_[i]; }
  // How the attempt for `episode` ended; `sent`: the modem answered +CMGS: and OK.
  SmsAttemptEnd attemptEnded(const std::string& episode, bool sent, uint32_t nowMs);
  // Drops `episode` without an attempt (e.g. the phone was removed meanwhile). False if gone.
  bool drop(const std::string& episode);

 private:
  SmsQueueResult once(EpisodeRing<kRemembered>& ring, const std::string& episode,
                      const std::string& alertType, bool handsOff, const EventPosition& where,
                      bool hasPhone, uint32_t nowMs);
  SmsQueueResult enqueue(const std::string& episode, const std::string& alertType, bool handsOff,
                         const EventPosition& where, uint32_t nowMs);
  int find(const std::string& episode) const;

  SmsTiming timing_;
  SmsMessage slots_[kSlots];
  EpisodeRing<kRemembered> episodes_;      // camera episodes that got (or skipped) their SMS
  EpisodeRing<kRemembered> gripEpisodes_;  // grip episodes likewise
};

// The SMS motive for a camera alert type (C5): "eyes_closed" -> "ojos cerrados", ...; nullptr for
// any other (the text then uses the raw alert type).
const char* smsMotivo(const std::string& alertType);

// The C5 text, plain GSM-7, at most kSmsMaxLength chars. It says what the backend's alert for the
// same critical says (route guardian contracts G9): the alert type (its motive, worded like the
// dashboard's label), the severity, the truck, the time and where:
//   "ARGUS ALERTA CRITICA: <motivo>[, sin manos en el volante]. Camion <vehicle>[, <HH:MM>].
//    https://maps.google.com/?q=<lat>,<lon>"
// (6 decimals), or "... Camion <vehicle>[, <HH:MM>]. Ubicacion desconocida." without a position.
// e.g. "ARGUS ALERTA CRITICA: ojos cerrados, sin manos en el volante. Camion JAL-123-A, 14:05.
// https://maps.google.com/?q=20.659712,-103.349612". The hands-off part only with `handsOff`;
// the grip critical (kGripAlertType, "no_grip") reads "ARGUS ALERTA CRITICA: sin manos en el
// volante. Camion ...". `vehicle` is the truck's plate, else its truck id. `clock` is the event's
// local time (smsClockText), "" = no GPS time: no time part. A character outside the safe GSM-7
// set (letters, digits, space and . , : / ? = + - # ( )) in the raw alert type, the vehicle or the
// clock is written as a space ("mouth_open" -> "mouth open"), so the modem's "GSM" character set
// can't garble it.
// The cut, always the same: the vehicle to kSmsVehicleMaxLength; then the time part only if the
// whole motive still fits with it (else it is left out); then the motive to what fits. The
// hands-off part and the link always fit whole.
constexpr size_t kSmsMaxLength = 160;
constexpr size_t kSmsVehicleMaxLength = 24;  // a truck id (ObjectId) is 24, a plate at most 16
std::string composeSmsText(const std::string& alertType, bool handsOff,
                           const std::string& vehicle, const EventPosition& where,
                           const std::string& clock = std::string());

// The event's UTC time, epoch seconds, from the GPS clock: `gpsEpoch` read at millis()
// `gpsAtMs`, the event at millis() `eventMs` (before or after it; millis() wrap-safe). 0 when the
// GPS has no time (gpsEpoch 0).
uint64_t smsEventEpoch(uint64_t gpsEpoch, uint32_t gpsAtMs, uint32_t eventMs);
// "HH:MM" of `epochUtc` shifted by `utcOffsetMinutes` (SMS_UTC_OFFSET_MINUTES); "" for 0.
std::string smsClockText(uint64_t epochUtc, int utcOffsetMinutes);
