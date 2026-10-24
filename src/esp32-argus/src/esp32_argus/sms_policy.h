#pragma once

// The critical-alert SMS (docs/designs/grip-alarm-contracts.md C5): which criticals get one, the
// queue that waits for the GSM network, the retries, and the text. Pure logic with an injected
// millis() clock, so test/host/sms_policy_host_test.cpp runs it on the PC with exact strings.
//
// - One SMS per critical episode: each grip episode, and each Pi critical episode first taken live
//   (a ring of the last SMS_EPISODE_MEMORY Pi episodes dedupes a record served again and the same
//   episode's later criticals). Never for a medium, an OK or a DLQ replay. A no_face critical gets
//   one while SMS_ON_NO_FACE is 1 (it never sounds the buzzer).
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
#include "pi_event_policy.h"

struct SmsTiming {
  uint32_t retryGapMs = SMS_RETRY_GAP_MS;
  uint32_t pendingMaxMs = SMS_PENDING_MAX_MS;
  int maxAttempts = SMS_MAX_ATTEMPTS;
  bool onNoFace = SMS_ON_NO_FACE != 0;
};

// One message waiting to go. A static slot: its text is fixed-capacity (bounded_string.h). The
// text itself is composed when it goes (composeSmsText), with the truck's plate as known then.
struct SmsMessage {
  bool used = false;
  IdString episode;
  AlertTypeString alertType;  // the alert's alert_type: the Pi's reason, or "no_grip"
  EventPosition position;     // where the truck was when the alert was raised
  uint32_t queuedAtMs = 0;
  int attempts = 0;           // failed tries so far
  uint32_t lastAttemptEndMs = 0;
};

enum class SmsQueued : uint8_t {
  Ignored,    // not a critical this rule sends an SMS for (medium, OK, DLQ, not taken, ...)
  Queued,
  Duplicate,  // this episode already had its SMS (or was skipped): nothing new
  NoPhone,    // no alert phone set: skipped (log it: once per episode)
  NoFace,     // a no_face critical with SMS_ON_NO_FACE 0
};

struct SmsQueueResult {
  SmsQueued result = SmsQueued::Ignored;
  bool evicted = false;  // the queue was full: the oldest waiting message was dropped for it
  IdString evictedEpisode;
};

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

  // A Pi record from a Bluetooth session, as pull_session.h decided it (like
  // AlarmPolicy::onPiRecord): only a live (`!dlq`), taken AlertCritical is an SMS. `where` is the
  // position composed into its event.
  SmsQueueResult onPiRecord(PiEventKind kind, bool dlq, bool taken, const std::string& episode,
                            const std::string& alertType, const EventPosition& where,
                            bool hasPhone, uint32_t nowMs);
  // A grip episode started (grip_policy.h): its SMS, alert type "no_grip".
  SmsQueueResult onGripEpisode(const std::string& episode, const EventPosition& where,
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
  SmsQueueResult enqueue(const std::string& episode, const std::string& alertType,
                         const EventPosition& where, uint32_t nowMs);
  int find(const std::string& episode) const;

  SmsTiming timing_;
  SmsMessage slots_[kSlots];
  EpisodeRing<kRemembered> piEpisodes_;
};

// The SMS motive for an alert type (C5): "eyes_closed" -> "ojos cerrados", ..., "no_grip" ->
// "manos fuera del volante"; nullptr for any other (the text then uses the raw alert type).
const char* smsMotivo(const std::string& alertType);

// The C5 text, plain GSM-7, at most kSmsMaxLength chars:
//   "ARGUS ALERTA CRITICA: <motivo>. Camion <vehicle>. https://maps.google.com/?q=<lat>,<lon>"
// (6 decimals), or "... Camion <vehicle>. Ubicacion desconocida." without a position. `vehicle`
// is the truck's plate, else its truck id. A character outside the safe GSM-7 set (letters,
// digits, space and . , : / ? = + - # ( )) in the raw alert type or the vehicle is written as a
// space ("mouth_open" -> "mouth open"), so the modem's "GSM" character set can't garble it. The
// vehicle is cut to kSmsVehicleMaxLength, and the motive to what then fits in 160 chars: the link
// always fits whole.
constexpr size_t kSmsMaxLength = 160;
constexpr size_t kSmsVehicleMaxLength = 24;  // a truck id (ObjectId) is 24, a plate at most 16
std::string composeSmsText(const std::string& alertType, const std::string& vehicle,
                           const EventPosition& where);
