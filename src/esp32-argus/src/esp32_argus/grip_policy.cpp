#include "grip_policy.h"

#include <cstdio>

bool gripHasPressure(const int* raw, int count, int threshold) {
  for (int i = 0; i < count; i++) {
    if (raw[i] > threshold) return true;
  }
  return false;
}

bool gripIsMoving(bool hasFix, float speedKmh, float minSpeedKmh) {
  return hasFix && speedKmh >= minSpeedKmh;
}

GripPolicy::GripPolicy(const GripTiming& timing) : timing_(timing) {}

GripChange GripPolicy::onSample(bool pressure, bool moving, uint32_t nowMs) {
  if (inEpisode_) {
    if (!pressure) {
      recovering_ = false;  // the recovery must be continuous
      return GripChange::None;
    }
    if (!recovering_) {
      recovering_ = true;
      pressureSince_ = nowMs;
    }
    if ((uint32_t)(nowMs - pressureSince_) < timing_.recoveryMs) return GripChange::None;
    inEpisode_ = false;
    recovering_ = false;
    counting_ = false;  // the next episode needs a full GRIP_ALERT_MS without pressure again
    return GripChange::EpisodeEnded;
  }

  if (pressure || (timing_.requireMoving && !moving)) {
    counting_ = false;
    return GripChange::None;
  }
  if (!counting_) {
    counting_ = true;
    noPressureSince_ = nowMs;
  }
  if ((uint32_t)(nowMs - noPressureSince_) < timing_.alertMs) return GripChange::None;
  inEpisode_ = true;
  counting_ = false;
  recovering_ = false;
  episode_++;
  return GripChange::EpisodeStarted;
}

uint32_t GripPolicy::noPressureMs(uint32_t nowMs) const {
  return counting_ ? (uint32_t)(nowMs - noPressureSince_) : 0;
}

std::string gripEpisodeId(uint32_t bootTag, uint32_t n) {
  char id[40];
  std::snprintf(id, sizeof(id), "grip-%08lx-%lu", (unsigned long)bootTag, (unsigned long)n);
  return id;
}

namespace {

ReportEvent gripEvent(uint32_t bootTag, uint32_t n, bool isOk, const EventPosition& position,
                      bool hasSpeed, float speedKmh) {
  std::string episode = gripEpisodeId(bootTag, n);
  ReportEvent ev;
  ev.clientEventId = episode + (isOk ? "-ok" : "-a");
  ev.episode = episode;
  ev.isOk = isOk;
  if (!isOk) {
    ev.severity = "critical";
    ev.alertType = kGripAlertType;
  }
  ev.position = position;
  ev.hasSpeed = hasSpeed;
  ev.speedKmh = speedKmh;
  return ev;  // model "" and no scores: ai_metadata null
}

}  // namespace

ReportEvent gripAlertEvent(uint32_t bootTag, uint32_t n, const EventPosition& position,
                           bool hasSpeed, float speedKmh) {
  return gripEvent(bootTag, n, false, position, hasSpeed, speedKmh);
}

ReportEvent gripOkEvent(uint32_t bootTag, uint32_t n, const EventPosition& position,
                        bool hasSpeed, float speedKmh) {
  return gripEvent(bootTag, n, true, position, hasSpeed, speedKmh);
}
