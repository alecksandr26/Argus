#pragma once

// When the cabin buzzer sounds (docs/designs/grip-alarm-contracts.md C4, changed 2026-11-03).
//
// - Never with the hands on the wheel: every path needs the grip alarm (grip_policy.h: on for the
//   whole grip episode, GRIP_ALERT_MS without pressure until GRIP_RECOVERY_MS with it).
// - Hands off + a camera critical (the vision alarm): sounds at once.
// - Hands off with the camera OK: sounds once the wheel has had no pressure for
//   GRIP_ONLY_BUZZER_MS (3 s from the last pressure since 2026-11-03, i.e. the grip episode's own
//   start). No movement gate: a parked truck counts too (user decision).
// - The no-face cap: while the camera's LATEST live reading says nobody is in view -- class
//   "No Face" (the Pi's first face-less reading, ~0.6 s after the face is gone) or a no_face
//   critical -- the buzzer, from either path, sounds NO_FACE_BUZZER_MAX_MS (3 s) at most, counted
//   from the later of the buzzer turning on and that reading arriving, then stays quiet until a
//   live reading with a face arrives (even drowsy: the normal rules apply again at once), or until
//   no live reading arrived for PI_STALE_MS (a dead link must never mute the wheel). It follows the
//   readings, keepalives included, not the events: the Pi sends no event for a reason change at
//   the same severity (no_face -> eyes_closed when the driver comes back asleep), so "the last
//   alert was no_face" would keep a sleeping driver muted.
//
// The vision alarm turns on when this device takes a live Pi record classified as a critical
// alert (C1.5), no_face included. It turns off on a live driver OK of its episode, or after
// VISION_ALARM_MAX_MS as a safety cap if that OK never comes. A medium never turns it on, a DLQ
// replay never does, and each episode turns it on at most once: a record the Pi serves again
// after its lease lapsed, or a later critical of the same episode, can't restart an alarm the
// driver OK (or the cap) already ended.
//
// The SMS is not decided here any more (every critical texts, sms_policy.h / cabin_signals.h).
//
// Pure logic with an injected millis() clock (test/host/alarm_policy_host_test.cpp); the sketch
// keeps one instance behind its state mutex, calls tick() every grip sample (100 ms) and
// buzzer.h writes the pin.

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

// Why the buzzer is in the state tick() last decided (for the log lines).
enum class BuzzerCause : uint8_t {
  None,           // silent: hands on the wheel, or hands off for less than the grip-only delay
  CameraAndGrip,  // a camera critical + the hands off
  GripOnly,       // the hands off GRIP_ONLY_BUZZER_MS with no camera critical
  NoFaceMuted,    // it would sound, but nobody is in view and it already sounded the 3 s
};

struct AlarmTiming {
  uint32_t visionMaxMs = VISION_ALARM_MAX_MS;
  // Into the grip episode (which starts GRIP_ALERT_MS after the last pressure).
  uint32_t gripOnlyAfterMs = GRIP_ONLY_BUZZER_MS - GRIP_ALERT_MS;
  uint32_t noFaceMaxMs = NO_FACE_BUZZER_MAX_MS;
  uint32_t cameraStaleMs = PI_STALE_MS;
};

class AlarmPolicy {
 public:
  // Episodes remembered as "already sounded" (the same ring size as the SMS dedupe).
  static constexpr int kRemembered = SMS_EPISODE_MEMORY;

  explicit AlarmPolicy(const AlarmTiming& timing = AlarmTiming()) : timing_(timing) {}
  explicit AlarmPolicy(uint32_t visionMaxMs) { timing_.visionMaxMs = visionMaxMs; }

  // A grip episode started (`on`) or ended, at `nowMs` (the grip-only delay counts from it).
  void setGripAlarm(bool on, uint32_t nowMs);

  // A Pi record from a Bluetooth session, as pull_session.h decided it: `kind` its C1.5 class,
  // `dlq` it came from `DLQ 1`, `taken` this device TAKEN it for the sender. `episode` and
  // `alertType` as the event has them (backend_messages.h eventEpisode / eventAlertType). A driver
  // OK stops the alarm whether or not it was taken; an OK of another (older) episode doesn't.
  VisionChange onPiRecord(PiEventKind kind, bool dlq, bool taken, const std::string& episode,
                          const std::string& alertType, uint32_t nowMs);

  // Every usable live record (keepalives and non-events included): the camera's latest
  // `className`, `severity` and `reason` (payload fields; "" if absent). Nobody in view =
  // class "No Face", or a critical with reason no_face (its class is "Drowsy"). A DLQ replay is
  // history: ignored.
  void onCameraReading(bool dlq, const std::string& className, const std::string& severity,
                       const std::string& reason, uint32_t nowMs);

  // Decides the buzzer at `nowMs` (read it under the same lock as the calls above). Returns true
  // when the VISION_ALARM_MAX_MS cap just turned the vision alarm off. A `nowMs` slightly before
  // a time another task stored (it read the clock before it took the lock) counts as no time
  // elapsed, not as a wrapped-around eternity.
  bool tick(uint32_t nowMs);

  // What the last tick() decided.
  bool buzzer() const { return buzzer_; }
  BuzzerCause cause() const { return cause_; }

  bool gripAlarm() const { return grip_; }
  bool visionAlarm() const { return vision_; }
  const IdString& visionEpisode() const { return visionEpisode_; }
  // The alert type of the critical the vision alarm is on for (the SMS names it).
  const AlertTypeString& visionAlertType() const { return visionAlertType_; }
  // The latest live reading said nobody is in view (as of the last call that changed it; tick()
  // applies the staleness).
  bool cameraNoFace() const { return cameraNoFace_; }

 private:
  AlarmTiming timing_;
  bool grip_ = false;
  uint32_t gripSinceMs_ = 0;
  bool vision_ = false;
  IdString visionEpisode_;
  AlertTypeString visionAlertType_;
  uint32_t visionSinceMs_ = 0;
  EpisodeRing<kRemembered> sounded_;

  bool cameraNoFace_ = false;
  uint32_t cameraSeenMs_ = 0;
  bool noFaceTiming_ = false;  // the buzzer sounded (or would) during this no_face stretch
  uint32_t noFaceSinceMs_ = 0;
  bool noFaceMuted_ = false;

  bool buzzer_ = false;
  BuzzerCause cause_ = BuzzerCause::None;
};
