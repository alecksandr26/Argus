#include "alarm_policy.h"

VisionChange AlarmPolicy::onPiRecord(PiEventKind kind, bool dlq, bool taken,
                                     const std::string& episode, const std::string& alertType,
                                     uint32_t nowMs) {
  if (dlq) return VisionChange::None;  // a replay is history: it never sounds

  if (kind == PiEventKind::AlertCritical) {
    if (!taken) return VisionChange::None;  // left on the Pi: it sounds when it is taken
    if (alertType == "no_face") return VisionChange::NoFace;
    if (sounded_.contains(episode)) return VisionChange::None;  // served again / same episode
    sounded_.add(episode);
    vision_ = true;
    visionEpisode_.assignTruncated(episode);
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

bool AlarmPolicy::tick(uint32_t nowMs) {
  if (!vision_ || (uint32_t)(nowMs - visionSinceMs_) < visionMaxMs_) return false;
  vision_ = false;
  return true;
}
