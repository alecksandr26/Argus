// Which position and which clock the ESP32 attaches to a relayed record.
// GPS first, but only a *fresh* fix (TinyGPSPlus keeps isValid() true after the fix is lost,
// GPS-02); then a recent SIM800L cell fix, marked as approximate (GPS-01); else nothing (the
// record waits). Time: GPS UTC, then network time (AT+CCLK), else let the server stamp it.
// SOURCES: location_policy.cpp
#include <string>

#include "check.h"
#include "location_policy.h"

static GpsSample gps(bool valid, uint32_t ageMs) { return {valid, ageMs, 20.6582, -103.3761}; }
static CellSample cell(bool ok, uint32_t ageMs) { return {ok, ageMs, 20.66, -103.37, 550}; }

static void freshGpsWins() {
  Location loc = chooseLocation(gps(true, 900), cell(true, 1000));
  CHECK(loc.source == LocationSource::Gps);
  CHECK_NEAR(loc.lat, 20.6582, 1e-9);
  CHECK(loc.accuracyM < 0);  // not reported for GPS
}

static void staleGpsFallsBackToCell() {
  Location loc = chooseLocation(gps(true, 6000), cell(true, 1000));
  CHECK(loc.source == LocationSource::Cell);
  CHECK(loc.accuracyM == 550);
}

static void gpsFreshnessBoundary() {
  CHECK(chooseLocation(gps(true, 4999), cell(false, 0)).source == LocationSource::Gps);
  CHECK(chooseLocation(gps(true, 5000), cell(false, 0)).source == LocationSource::None);
}

static void staleCellIsNotUsed() {
  Location loc = chooseLocation(gps(false, 0), cell(true, 10 * 60 * 1000));
  CHECK(loc.source == LocationSource::None);
}

static void nothingAvailable() {
  CHECK(chooseLocation(gps(false, 0), cell(false, 0)).source == LocationSource::None);
}

static void timeSourcePriority() {
  CHECK(chooseTimeSource(true, true) == TimeSource::Gps);
  CHECK(chooseTimeSource(false, true) == TimeSource::Network);
  CHECK(chooseTimeSource(false, false) == TimeSource::Server);
}

static void sourceNamesMatchTheBackendEnum() {
  CHECK(std::string(locationSourceName(LocationSource::Gps)) == "gps");
  CHECK(std::string(locationSourceName(LocationSource::Cell)) == "cell");
  CHECK(std::string(timeSourceName(TimeSource::Network)) == "network");
  CHECK(std::string(timeSourceName(TimeSource::Server)) == "server");
}

int main() {
  RUN(freshGpsWins);
  RUN(staleGpsFallsBackToCell);
  RUN(gpsFreshnessBoundary);
  RUN(staleCellIsNotUsed);
  RUN(nothingAvailable);
  RUN(timeSourcePriority);
  RUN(sourceNamesMatchTheBackendEnum);
  return report("location_policy_host_test");
}
