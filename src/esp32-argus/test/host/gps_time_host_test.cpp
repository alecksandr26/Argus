// When GPS time may be used as an event time or resolved_at (GPS-07a/b).
// - GPS-07a: NEO-6M modules with old firmware report dates ~19.6 years in the past after the
//   GPS week-number rollover (e.g. 1999 or 2006). The backend rejects a resolved_at earlier than
//   the alert (422) and an occurred_at older than its 7-day backlog window, so a rolled-over date
//   would wedge every resolve and, once occurred_at is sent, every record.
// - GPS-07b: TinyGPSPlus keeps date/time isValid() after the signal is lost, like the position
//   (GPS-02), so a stale time must be treated as no time.
// An unusable GPS time falls through chooseTimeSource() to network time, then server time.
// SOURCES: location_policy.cpp
#include "check.h"
#include "location_policy.h"

static GpsTimeSample at(int year, uint32_t ageMs, bool valid = true) { return {valid, ageMs, year}; }

static void currentDateIsUsable() { CHECK(gpsTimeUsable(at(2026, 900))); }

static void weekRolloverDatesAreRejected() {
  CHECK(!gpsTimeUsable(at(1999, 100)));
  CHECK(!gpsTimeUsable(at(2006, 100)));
  CHECK(!gpsTimeUsable(at(2019, 100)));
}

static void minimumYearBoundary() {
  CHECK(!gpsTimeUsable(at(GPS_TIME_MIN_YEAR - 1, 100)));
  CHECK(gpsTimeUsable(at(GPS_TIME_MIN_YEAR, 100)));
}

static void staleTimeIsRejected() {
  CHECK(gpsTimeUsable(at(2026, 4999)));
  CHECK(!gpsTimeUsable(at(2026, 5000)));  // same freshness limit as the position
}

static void invalidTimeIsRejected() { CHECK(!gpsTimeUsable(at(2026, 100, /*valid=*/false))); }

static void rolledOverGpsFallsBackToNetworkThenServer() {
  bool gpsOk = gpsTimeUsable(at(2006, 100));
  CHECK(chooseTimeSource(gpsOk, true) == TimeSource::Network);
  CHECK(chooseTimeSource(gpsOk, false) == TimeSource::Server);
}

int main() {
  RUN(currentDateIsUsable);
  RUN(weekRolloverDatesAreRejected);
  RUN(minimumYearBoundary);
  RUN(staleTimeIsRejected);
  RUN(invalidTimeIsRejected);
  RUN(rolledOverGpsFallsBackToNetworkThenServer);
  return report("gps_time_host_test");
}
