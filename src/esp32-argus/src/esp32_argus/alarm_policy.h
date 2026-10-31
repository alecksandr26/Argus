#pragma once

// When the cabin buzzer sounds (docs/designs/grip-alarm-contracts.md C4, changed 2026-10-31): only
// while the grip alarm AND the vision alarm are both on -- a double check, so neither the camera
// alone nor the wheel alone wakes the cab. Either one ending stops it at once.
//
// - The grip alarm follows grip_policy.h: on for the whole grip episode (GRIP_ALERT_MS without
//   pressure until GRIP_RECOVERY_MS with it).
// - The vision alarm turns on when this device takes a live Pi record classified as a critical
//   alert (C1.5), no_face included (a driver out of view with no hands on the wheel is the worst
//   case). It turns off on a live driver OK of its episode, or after VISION_ALARM_MAX_MS as a
//   safety cap if that OK never comes. A medium never sounds, a DLQ replay never sounds, and each
//   episode turns it on at most once: a record the Pi serves again after its lease lapsed, or a
//   later critical of the same episode, can't restart an alarm the driver OK (or the cap) already
//   ended.
//
// Pure logic with an injected millis() clock (test/host/alarm_policy_host_test.cpp); the sketch
// keeps one instance behind its state mutex and buzzer.h writes the pin.

#include <cstdint>
#include <string>

#include "bounded_string.h"
#include "config.h"
#include "episode_ring.h"
#include "pi_event_policy.h"

enum class VisionChange : uint8_t {
  None,
  On,       // a new episode's critical: the vision alarm turns on (or moves to this episode)
  Off,      // the driver OK of the episode it is on for
};

class AlarmPolicy {
 public:
  // Episodes remembered as "already sounded" (the same ring size as the SMS dedupe).
  static constexpr int kRemembered = SMS_EPISODE_MEMORY;

  explicit AlarmPolicy(uint32_t visionMaxMs = VISION_ALARM_MAX_MS) : visionMaxMs_(visionMaxMs) {}

  void setGripAlarm(bool on) { grip_ = on; }

  // A Pi record from a Bluetooth session, as pull_session.h decided it: `kind` its C1.5 class,
  // `dlq` it came from `DLQ 1`, `taken` this device TAKEN it for the sender. `episode` and
  // `alertType` as the event has them (backend_messages.h eventEpisode / eventAlertType). A driver
  // OK stops the alarm whether or not it was taken; an OK of another (older) episode doesn't.
  VisionChange onPiRecord(PiEventKind kind, bool dlq, bool taken, const std::string& episode,
                          const std::string& alertType, uint32_t nowMs);

  // The VISION_ALARM_MAX_MS cap: true when it just turned the vision alarm off. A `nowMs` slightly
  // before the critical's own time (read by another task before it took the lock) counts as no
  // time elapsed, not as a wrapped-around eternity.
  bool tick(uint32_t nowMs);

  // The buzzer, and the moment the critical-alert SMS is queued (cabin_signals.h): both alarms on.
  bool buzzer() const { return grip_ && vision_; }
  bool gripAlarm() const { return grip_; }
  bool visionAlarm() const { return vision_; }
  const IdString& visionEpisode() const { return visionEpisode_; }
  // The alert type of the critical the vision alarm is on for (the SMS names it).
  const AlertTypeString& visionAlertType() const { return visionAlertType_; }

 private:
  uint32_t visionMaxMs_;
  bool grip_ = false;
  bool vision_ = false;
  IdString visionEpisode_;
  AlertTypeString visionAlertType_;
  uint32_t visionSinceMs_ = 0;
  EpisodeRing<kRemembered> sounded_;
};
