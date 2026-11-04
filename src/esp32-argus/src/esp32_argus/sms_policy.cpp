#include "sms_policy.h"

#include <cstdio>

namespace {

bool reached(uint32_t nowMs, uint32_t dueMs) { return (int32_t)(nowMs - dueMs) >= 0; }

// The characters that mean the same in ASCII and in the GSM 7-bit default alphabet the modem
// uses with AT+CSCS="GSM" (3GPP TS 23.038): '_', '@', '$' and the brackets don't.
bool gsmSafe(char c) {
  if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')) return true;
  switch (c) {
    case ' ': case '.': case ',': case ':': case '/': case '?': case '=': case '+': case '-':
    case '#': case '(': case ')':
      return true;
    default:
      return false;
  }
}

std::string gsmSanitized(const std::string& text) {
  std::string out = text;
  for (char& c : out) {
    if (!gsmSafe(c)) c = ' ';
  }
  return out;
}

}  // namespace

SmsPolicy::SmsPolicy(const SmsTiming& timing) : timing_(timing) {}

int SmsPolicy::find(const std::string& episode) const {
  for (int i = 0; i < kSlots; i++) {
    if (slots_[i].used && slots_[i].episode == episode) return i;
  }
  return -1;
}

SmsQueueResult SmsPolicy::onCameraCritical(const std::string& visionEpisode,
                                           const std::string& alertType, bool handsOff,
                                           const EventPosition& where, bool hasPhone,
                                           uint32_t nowMs) {
  return once(episodes_, visionEpisode, alertType, handsOff, where, hasPhone, nowMs);
}

SmsQueueResult SmsPolicy::onGripCritical(const std::string& gripEpisode,
                                         const EventPosition& where, bool hasPhone,
                                         uint32_t nowMs) {
  return once(gripEpisodes_, gripEpisode, kGripAlertType, true, where, hasPhone, nowMs);
}

SmsQueueResult SmsPolicy::once(EpisodeRing<kRemembered>& ring, const std::string& episode,
                               const std::string& alertType, bool handsOff,
                               const EventPosition& where, bool hasPhone, uint32_t nowMs) {
  SmsQueueResult r;
  if (ring.contains(episode)) {
    r.result = SmsQueued::Duplicate;  // served again, or a later critical of the episode
    return r;
  }
  ring.add(episode);
  if (!hasPhone) {
    r.result = SmsQueued::NoPhone;
    return r;
  }
  return enqueue(episode, alertType, handsOff, where, nowMs);
}

SmsQueueResult SmsPolicy::enqueue(const std::string& episode, const std::string& alertType,
                                  bool handsOff, const EventPosition& where, uint32_t nowMs) {
  SmsQueueResult r;
  if (find(episode) >= 0) {
    r.result = SmsQueued::Duplicate;
    return r;
  }
  int free = -1;
  int oldest = -1;
  for (int i = 0; i < kSlots; i++) {
    if (!slots_[i].used) {
      if (free < 0) free = i;
    } else if (oldest < 0 ||
               (int32_t)(slots_[i].queuedAtMs - slots_[oldest].queuedAtMs) < 0) {
      oldest = i;
    }
  }
  if (free < 0) {
    // Full: the newest alert says where the truck is now, so the oldest waiting one goes.
    r.evicted = true;
    r.evictedEpisode = slots_[oldest].episode;
    free = oldest;
  }
  SmsMessage& m = slots_[free];
  m = SmsMessage();
  m.used = true;
  m.episode.assignTruncated(episode);
  m.alertType.assignTruncated(alertType);
  m.handsOff = handsOff;
  m.position = where;
  m.queuedAtMs = nowMs;
  r.result = SmsQueued::Queued;
  r.handsOff = handsOff;
  r.queuedAtMs = nowMs;
  return r;
}

bool SmsPolicy::expireOne(uint32_t nowMs, SmsMessage& dropped) {
  for (SmsMessage& m : slots_) {
    if (m.used && (uint32_t)(nowMs - m.queuedAtMs) >= timing_.pendingMaxMs) {
      dropped = m;
      m = SmsMessage();
      return true;
    }
  }
  return false;
}

int SmsPolicy::due(uint32_t nowMs) const {
  int best = -1;
  for (int i = 0; i < kSlots; i++) {
    const SmsMessage& m = slots_[i];
    if (!m.used) continue;
    if (m.attempts > 0 && !reached(nowMs, m.lastAttemptEndMs + timing_.retryGapMs)) continue;
    if (best < 0 || (int32_t)(m.queuedAtMs - slots_[best].queuedAtMs) < 0) best = i;
  }
  return best;
}

bool SmsPolicy::pending() const {
  for (const SmsMessage& m : slots_) {
    if (m.used) return true;
  }
  return false;
}

SmsAttemptEnd SmsPolicy::attemptEnded(const std::string& episode, bool sent, uint32_t nowMs) {
  int i = find(episode);
  if (i < 0) return SmsAttemptEnd::Gone;
  SmsMessage& m = slots_[i];
  if (sent) {
    m = SmsMessage();
    return SmsAttemptEnd::Sent;
  }
  m.attempts++;
  m.lastAttemptEndMs = nowMs;
  if (m.attempts >= timing_.maxAttempts) {
    m = SmsMessage();
    return SmsAttemptEnd::GaveUp;
  }
  return SmsAttemptEnd::Retry;
}

bool SmsPolicy::drop(const std::string& episode) {
  int i = find(episode);
  if (i < 0) return false;
  slots_[i] = SmsMessage();
  return true;
}

const char* smsMotivo(const std::string& alertType) {
  if (alertType == "eyes_closed") return "ojos cerrados";
  if (alertType == "yawn_eyes_closed") return "ojos cerrados tras bostezo";
  if (alertType == "head_down") return "cabeza abajo";
  if (alertType == "head_down_eyes_closed") return "cabeza abajo y ojos cerrados";
  if (alertType == "no_face") return "conductor no visible";
  return nullptr;
}

const char* smsSkipReason(SmsQueued result) {
  switch (result) {
    case SmsQueued::NoPhone:
      return "no usable alert phone from the Pi's CONFIG (ARGUS_ALERT_PHONE in argus-device.env: "
             "missing, or not E.164)";
    case SmsQueued::Duplicate:
      return "this episode already had its SMS (or was skipped)";
    case SmsQueued::Queued:
    case SmsQueued::Ignored:
      break;
  }
  return nullptr;
}

uint64_t smsEventEpoch(uint64_t gpsEpoch, uint32_t gpsAtMs, uint32_t eventMs) {
  if (gpsEpoch == 0) return 0;
  int64_t s = (int64_t)gpsEpoch + (int64_t)(int32_t)(eventMs - gpsAtMs) / 1000;
  return s > 0 ? (uint64_t)s : 0;
}

std::string smsClockText(uint64_t epochUtc, int utcOffsetMinutes) {
  if (epochUtc == 0) return std::string();
  int64_t minutes = ((int64_t)(epochUtc / 60) + utcOffsetMinutes) % (24 * 60);
  if (minutes < 0) minutes += 24 * 60;
  char text[12];
  std::snprintf(text, sizeof(text), "%02d:%02d", (int)(minutes / 60), (int)(minutes % 60));
  return text;
}

std::string composeSmsText(const std::string& alertType, bool handsOff,
                           const std::string& vehicle, const EventPosition& where,
                           const std::string& clock) {
  const std::string handsPart = "sin manos en el volante";
  bool grip = alertType == kGripAlertType;
  std::string motivo;
  if (!grip) {
    const char* known = smsMotivo(alertType);
    motivo = known ? known : gsmSanitized(alertType);
    if (motivo.empty()) motivo = "desconocido";
  }
  std::string truck = vehicle.empty() ? std::string("desconocido") : gsmSanitized(vehicle);

  std::string place;
  if (where.known) {
    char link[80];
    std::snprintf(link, sizeof(link), "https://maps.google.com/?q=%.6f,%.6f", where.lat,
                  where.lon);
    place = link;
  } else {
    place = "Ubicacion desconocida.";  // never wait for a fix
  }

  const std::string head = "ARGUS ALERTA CRITICA: ";
  // The grip critical is the hands-off part alone; a camera one adds it when the hands are off.
  const std::string mid = grip       ? handsPart + ". Camion "
                          : handsOff ? ", " + handsPart + ". Camion "
                                     : std::string(". Camion ");
  const std::string tail = ". " + place;
  // Over 160 chars only with a long raw alert type (up to 64), a truck id or the time: cut those,
  // never the hands-off part or the link. The vehicle keeps kSmsVehicleMaxLength (a plate is at
  // most 16, a truck id 24); the time goes only if the whole motive fits with it; then the motive
  // gets what is left (at least 29 chars with a real position: "cabeza abajo y ojos cerrados",
  // the longest known one, is 28).
  if (truck.size() > kSmsVehicleMaxLength) truck.resize(kSmsVehicleMaxLength);
  std::string when = clock.empty() ? std::string() : ", " + gsmSanitized(clock);
  size_t fixed = head.size() + mid.size() + tail.size() + truck.size();
  if (fixed + when.size() + motivo.size() > kSmsMaxLength) when.clear();
  fixed += when.size();
  size_t room = kSmsMaxLength > fixed ? kSmsMaxLength - fixed : 0;
  if (motivo.size() > room) motivo.resize(room);
  return head + motivo + mid + truck + when + tail;
}
