#pragma once

// The GPS side of a report (final-tweaks plan section 1 "GPS"; contracts C2.1): one point per GPS
// read (every GPS_READ_INTERVAL_MS) since the last report, the newest REPORT_MAX_POINTS kept; with
// no fix, the last good position marked "last known" with its age; and each point's vigilance from
// the Pi's latest severity. Pure logic with an injected millis() clock, so
// test/host/gps_track_host_test.cpp runs it on the PC; gps_module.cpp feeds it the NEO-6M and the
// sketch keeps the last known position in NVS.

#include <cstdint>
#include <string>

#include "backend_messages.h"
#include "config.h"
#include "pi_event_policy.h"

// Points since the last report. The sender takes them all for a report; a report that fails
// drops them (user decision: the path has gaps after failures), a preempted one puts them back.
class TrackBuffer {
 public:
  static constexpr int kCapacity = REPORT_MAX_POINTS;

  void add(const TrackPoint& point);  // the oldest is dropped when full
  int size() const { return count_; }
  // Moves every point out, oldest first, into `out` (room for kCapacity); returns how many.
  int take(TrackPoint* out);
  // Puts back the points of a preempted report: older than anything added since, and only the
  // newest kCapacity overall are kept.
  void restore(const TrackPoint* points, int count);

 private:
  TrackPoint points_[kCapacity];
  int head_ = 0;
  int count_ = 0;
};

// The last good GPS position, so a point (and an event) can still say where the truck was when
// the fix is lost. Saved to NVS (serialize()) every GPS_LAST_KNOWN_SAVE_MS while there's a fix
// and at once when the fix is lost, together with the fix's GPS time (epoch seconds), so its age
// is known after a reboot too.
class LastKnownPosition {
 public:
  explicit LastKnownPosition(uint32_t saveEveryMs = GPS_LAST_KNOWN_SAVE_MS)
      : saveEveryMs_(saveEveryMs) {}

  // Every GPS read. `gpsEpochS` is the GPS's UTC time now (0 = not known). Returns true when the
  // caller should write serialize() to NVS now.
  bool onRead(bool hasFix, double lat, double lon, uint64_t gpsEpochS, uint32_t nowMs);

  // The last known position and its age now: from this boot's millis() when the fix was seen
  // this boot, else from GPS time (the saved fix's vs `gpsEpochNowS`). False when there is none,
  // or when its age can't be known (a position restored from NVS before GPS time is back).
  bool lookup(uint32_t nowMs, uint64_t gpsEpochNowS, double& lat, double& lon, float& ageS) const;

  bool known() const { return valid_; }

  // "LK1;<lat>;<lon>;<gps epoch s>". deserialize() returns false (and stays empty) on anything
  // else; a restored position has no millis() reference (it's from an earlier boot).
  std::string serialize() const;
  bool deserialize(const std::string& blob);

 private:
  uint32_t saveEveryMs_;
  bool valid_ = false;
  double lat_ = 0.0;
  double lon_ = 0.0;
  uint64_t fixEpochS_ = 0;   // GPS time of the fix, 0 = unknown
  bool fixThisBoot_ = false;
  uint32_t fixAtMs_ = 0;     // millis() of the fix (this boot only)
  bool hadFix_ = false;      // the previous read had a fix
  bool dirty_ = false;       // changed since the last save
  bool everSaved_ = false;
  uint32_t lastSaveMs_ = 0;
};

// The Pi's latest severity, for each point's `vigilance` (C2.1): "unknown" when no Pi record
// arrived for PI_STALE_MS, or none since boot.
class VigilanceTracker {
 public:
  explicit VigilanceTracker(uint32_t staleMs = PI_STALE_MS) : staleMs_(staleMs) {}
  void onPiRecord(PiSeverity severity, uint32_t nowMs);
  const char* vigilance(uint32_t nowMs) const;
  PiSeverity latest() const { return latest_; }

 private:
  uint32_t staleMs_;
  bool seen_ = false;
  uint32_t atMs_ = 0;
  PiSeverity latest_ = PiSeverity::Unknown;
};

// The point for one GPS read: the current fix (speed as read), else the last known position
// (speed 0, last_known, its age), else none (false). Also what an event's `position` is when it
// is composed (eventPosition).
bool trackPointFor(const GpsSnapshot& gps, const LastKnownPosition& lastKnown, uint64_t gpsEpochS,
                   const char* vigilance, uint32_t nowMs, TrackPoint& out);
EventPosition eventPosition(const GpsSnapshot& gps, const LastKnownPosition& lastKnown,
                            uint64_t gpsEpochS, uint32_t nowMs);

// UTC calendar time -> epoch seconds (0 for a date before 2024: a GPS week-rollover or no date).
uint64_t epochFromUtc(int year, int month, int day, int hour, int minute, int second);
