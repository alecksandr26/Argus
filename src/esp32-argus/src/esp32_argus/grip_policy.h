#pragma once

// The steering-wheel grip rule (docs/designs/grip-alarm-contracts.md C2) and the events it raises
// for the backend (C3). The user's rule, from their bench sketch: no pressure on any of the four
// RFP602 sensors for GRIP_ALERT_MS (3 s) starts a grip episode -- the grip alarm (the buzzer per
// alarm_policy.h), one live critical `no_grip` for the backend and its SMS; pressure back for
// GRIP_RECOVERY_MS (300 ms) ends it -- the
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

// Pressure = any sensor reads above `threshold` (the bench sketch's fixed rule; the sketch now
// uses GripBaseline below).
bool gripHasPressure(const int* raw, int count, int threshold);

// Each sensor's resting level, and pressure relative to it (changed 2026-11-03, C2): pressure =
// any sensor at least `delta` above its own baseline. The baselines are the average of the first
// `calibrationMs` of samples, then follow the reading only while it shows no pressure: slowly
// (EMA 0.02) near the baseline, fast (EMA 0.3) when it drops more than delta/2 below it (a hand
// that rested on the wheel at boot, then lifted). A sensor at or above baseline + delta is never
// learned, so a notebook or a resting hand keeps counting as pressure for as long as it stays.
// During calibration it reports no pressure (the grip timer runs from boot, as before).
class GripBaseline {
 public:
  static constexpr int kMaxSensors = 8;
  explicit GripBaseline(int count = GRIP_SENSOR_COUNT, int delta = GRIP_PRESSURE_DELTA,
                        uint32_t calibrationMs = GRIP_BASELINE_BOOT_MS);

  // One sample of `raw` (count readings) at `nowMs`: true = pressure.
  bool onSample(const int* raw, uint32_t nowMs);

  bool calibrated() const { return calibrated_; }
  int baseline(int i) const { return (int)(base_[i] + 0.5f); }
  int delta() const { return delta_; }

 private:
  int count_;
  int delta_;
  uint32_t calibrationMs_;
  bool started_ = false;
  bool calibrated_ = false;
  uint32_t startMs_ = 0;
  int samples_ = 0;
  float sum_[kMaxSensors] = {};
  float base_[kMaxSensors] = {};
};

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
