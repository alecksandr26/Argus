#pragma once

// The steering-wheel grip rule (docs/designs/grip-alarm-contracts.md C2) and the events it raises
// for the backend (C3). The user's rule, from their bench sketch: no pressure on any of the four
// RFP602 sensors for GRIP_ALERT_MS (3 s) starts a grip episode -- the buzzer, one live critical
// `no_grip` for the backend, one SMS; pressure back for GRIP_RECOVERY_MS (300 ms) ends it -- the
// grip alarm stops at once and one live OK goes. With GRIP_REQUIRE_MOVING the 3 s timer only runs
// while the truck moves. Grip is its own critical source: fusion.* stays unused.
//
// Pure logic with an injected millis() clock, so test/host/grip_policy_host_test.cpp runs it on
// the PC; grip_sensor.cpp reads the pins and the sketch's gripTask samples every
// GRIP_SAMPLE_INTERVAL_MS. One instance, used by that task only.

#include <cstdint>
#include <string>

#include "backend_messages.h"
#include "config.h"

// Pressure = any sensor reads above `threshold` (C2).
bool gripHasPressure(const int* raw, int count, int threshold = GRIP_PRESSURE_THRESHOLD);

// "The truck moves" for GRIP_REQUIRE_MOVING: a current GPS fix with at least minSpeedKmh. No fix
// counts as not moving.
bool gripIsMoving(bool hasFix, float speedKmh, float minSpeedKmh = GRIP_MIN_SPEED_KMH);

struct GripTiming {
  uint32_t alertMs = GRIP_ALERT_MS;
  uint32_t recoveryMs = GRIP_RECOVERY_MS;
  bool requireMoving = GRIP_REQUIRE_MOVING != 0;
};

enum class GripChange : uint8_t {
  None,
  EpisodeStarted,  // the grip alarm turned on: queue the critical and the SMS
  EpisodeEnded,    // the grip alarm turned off: queue the OK
};

class GripPolicy {
 public:
  explicit GripPolicy(const GripTiming& timing = GripTiming());

  // One sample. `moving` only matters with requireMoving: while it is false the no-pressure timer
  // doesn't run (it starts again from zero once the truck moves). An episode already running
  // ends only on pressure, moving or not.
  GripChange onSample(bool pressure, bool moving, uint32_t nowMs);

  bool alarm() const { return inEpisode_; }
  // The current (or last) episode's number, from 1; 0 before the first. Part of its ids (C3).
  uint32_t episode() const { return episode_; }
  // How long the wheel has had no pressure (0 while it has, or while the timer is held).
  uint32_t noPressureMs(uint32_t nowMs) const;

 private:
  GripTiming timing_;
  bool inEpisode_ = false;
  uint32_t episode_ = 0;
  bool counting_ = false;       // no pressure since noPressureSince_ (outside an episode)
  uint32_t noPressureSince_ = 0;
  bool recovering_ = false;     // pressure since pressureSince_ (inside an episode)
  uint32_t pressureSince_ = 0;
};

// The ids of grip episode `n` (C3): "grip-<boot>-<n>", its alert "grip-<boot>-<n>-a" and its OK
// "grip-<boot>-<n>-ok". `bootTag` is esp_random() at boot, written as 8 hex digits, so the ids
// never repeat across reboots (the backend de-duplicates by client_event_id).
std::string gripEpisodeId(uint32_t bootTag, uint32_t n);

// The C2.1 events: a live critical `no_grip` alert, and its episode's OK. `position`/`speed` are
// the GPS when it was raised (eventPosition(): the current fix, else the last known position, else
// unknown). No ai_metadata: nothing from the camera is in them.
ReportEvent gripAlertEvent(uint32_t bootTag, uint32_t n, const EventPosition& position,
                           bool hasSpeed, float speedKmh);
ReportEvent gripOkEvent(uint32_t bootTag, uint32_t n, const EventPosition& position,
                        bool hasSpeed, float speedKmh);

// The alert type of a grip alert: the backend takes it as is (contracts C1.1).
constexpr const char* kGripAlertType = "no_grip";
