#include "alarm_policy.h"

namespace {

// Wrap-safe "at least `span` ms from `since` to `now`" (signed): a `now` read a moment before
// `since` was stored by another task is "not yet", never ~49 days late.
bool elapsed(uint32_t nowMs, uint32_t sinceMs, uint32_t spanMs) {
  return (int32_t)(nowMs - (sinceMs + spanMs)) >= 0;
}

}  // namespace

void AlarmPolicy::setGripAlarm(bool on, uint32_t nowMs) {
  if (on && !grip_) gripSinceMs_ = nowMs;
  grip_ = on;
}

VisionChange AlarmPolicy::onPiRecord(PiEventKind kind, bool dlq, bool taken,
                                     const std::string& episode, const std::string& alertType,
                                     uint32_t nowMs) {
  if (dlq) return VisionChange::None;  // a replay is history: it never sounds

  if (kind == PiEventKind::AlertCritical) {
    if (!taken) return VisionChange::None;  // left on the Pi: it sounds when it is taken
    if (sounded_.contains(episode)) return VisionChange::None;  // served again / same episode
    sounded_.add(episode);
    vision_ = true;
    visionEpisode_.assignTruncated(episode);
    visionAlertType_.assignTruncated(alertType);
    visionSinceMs_ = nowMs;  // the cap counts from the newest episode's critical
    return VisionChange::On;
  }

  if (kind == PiEventKind::DriverOk && vision_) {
    // Only the sounding episode's OK (or one we can't tell apart) stops it: an older episode's
    // OK served again must not silence a newer alarm.
    if (!episode.empty() && !visionEpisode_.empty() && visionEpisode_ != episode) {
      return VisionChange::None;
    }
    vision_ = false;
    return VisionChange::Off;
  }
  return VisionChange::None;  // a medium, a keepalive, a non-event
}

void AlarmPolicy::onCameraReading(bool dlq, const std::string& className,
                                  const std::string& severity, const std::string& reason,
                                  uint32_t nowMs) {
  if (dlq) return;
  cameraNoFace_ = className == "No Face" || (severity == "critical" && reason == "no_face");
  cameraSeenMs_ = nowMs;
}

bool AlarmPolicy::tick(uint32_t nowMs) {
  bool capped = false;
  if (vision_ && elapsed(nowMs, visionSinceMs_, timing_.visionMaxMs)) {
    vision_ = false;
    capped = true;
  }

  bool gripOnly = grip_ && elapsed(nowMs, gripSinceMs_, timing_.gripOnlyAfterMs);
  bool wants = grip_ && (vision_ || gripOnly);

  // The no-face cap, on a fresh no_face reading only: a stale one (no link) never mutes.
  bool noFace = cameraNoFace_ && !elapsed(nowMs, cameraSeenMs_, timing_.cameraStaleMs);
  if (!noFace) {
    noFaceTiming_ = false;
    noFaceMuted_ = false;
  } else if (wants && !noFaceMuted_) {
    if (!noFaceTiming_) {
      // The later of "the buzzer turned on" and "the no_face reading arrived".
      noFaceTiming_ = true;
      noFaceSinceMs_ = nowMs;
    }
    if (elapsed(nowMs, noFaceSinceMs_, timing_.noFaceMaxMs)) noFaceMuted_ = true;
  }

  buzzer_ = wants && !noFaceMuted_;
  if (buzzer_) {
    cause_ = vision_ ? BuzzerCause::CameraAndGrip : BuzzerCause::GripOnly;
  } else {
    cause_ = (wants && noFaceMuted_) ? BuzzerCause::NoFaceMuted : BuzzerCause::None;
  }
  return capped;
}
