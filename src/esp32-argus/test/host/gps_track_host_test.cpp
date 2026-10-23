// The GPS side of a report (final-tweaks plan "GPS"; contracts C2.1): points since the last
// report, the last known position when there's no fix (with its age, across a reboot too), and
// each point's vigilance from the Pi.
// SOURCES: gps_track.cpp pi_event_policy.cpp
#include <string>

#include "check.h"
#include "gps_track.h"

static TrackPoint pt(uint32_t atMs) {
  TrackPoint p;
  p.atMs = atMs;
  p.lat = atMs;
  return p;
}

static GpsSnapshot fix(double lat = 20.5, double lon = -103.25, float speed = 55.0f) {
  GpsSnapshot g;
  g.hasFix = true;
  g.lat = lat;
  g.lon = lon;
  g.speedKmh = speed;
  return g;
}

// --- TrackBuffer ---

static void keepsTheNewestTenOldestFirst() {
  TrackBuffer t;
  for (uint32_t i = 1; i <= 13; i++) t.add(pt(i * 1000));
  CHECK(t.size() == TrackBuffer::kCapacity);
  TrackPoint out[TrackBuffer::kCapacity];
  int n = t.take(out);
  CHECK(n == 10);
  CHECK(out[0].atMs == 4000);
  CHECK(out[9].atMs == 13000);
  CHECK(t.size() == 0);
  CHECK(t.take(out) == 0);
}

static void aPreemptedReportsPointsGoBackFirst() {
  TrackBuffer t;
  t.add(pt(1000));
  t.add(pt(2000));
  TrackPoint taken[TrackBuffer::kCapacity];
  int n = t.take(taken);
  t.add(pt(3000));  // read while the preempted attempt was on its way
  t.restore(taken, n);
  TrackPoint out[TrackBuffer::kCapacity];
  CHECK(t.take(out) == 3);
  CHECK(out[0].atMs == 1000 && out[1].atMs == 2000 && out[2].atMs == 3000);
}

static void restoreStillKeepsOnlyTheNewestTen() {
  TrackBuffer t;
  TrackPoint old[8];
  for (int i = 0; i < 8; i++) old[i] = pt(1000 + i);
  for (uint32_t i = 0; i < 5; i++) t.add(pt(2000 + i));
  t.restore(old, 8);
  TrackPoint out[TrackBuffer::kCapacity];
  CHECK(t.take(out) == 10);
  CHECK(out[0].atMs == 1003);  // the 3 oldest restored ones are dropped
  CHECK(out[9].atMs == 2004);
}

// --- LastKnownPosition ---

static void firstFixIsSavedThenEverySixtySeconds() {
  LastKnownPosition lk;
  CHECK(!lk.known());
  CHECK(lk.onRead(true, 20.5, -103.25, 1800000000, 1000));  // first fix: save
  CHECK(!lk.onRead(true, 20.6, -103.25, 1800000001, 2000));
  CHECK(!lk.onRead(true, 20.7, -103.25, 1800000060, 60999));
  CHECK(lk.onRead(true, 20.8, -103.25, 1800000061, 61000));  // 60 s since the last save
}

static void losingTheFixSavesTheLastPositionOnce() {
  LastKnownPosition lk;
  lk.onRead(true, 20.5, -103.25, 1800000000, 1000);
  lk.onRead(true, 20.6, -103.20, 1800000005, 6000);  // not saved yet (throttled)
  CHECK(lk.onRead(false, 0, 0, 0, 7000));             // fix lost: save now
  CHECK(!lk.onRead(false, 0, 0, 0, 8000));            // and only once
  LastKnownPosition back;
  CHECK(back.deserialize(lk.serialize()));
  double lat = 0, lon = 0;
  float age = 0;
  CHECK(back.lookup(0, 1800000065, lat, lon, age));
  CHECK_NEAR(lat, 20.6, 1e-6);
  CHECK_NEAR(lon, -103.20, 1e-6);
  CHECK_NEAR(age, 60.0, 1e-3);  // from the fix's GPS time
}

static void losingTheFixRightAfterASaveDoesNotSaveAgain() {
  LastKnownPosition lk;
  lk.onRead(true, 20.5, -103.25, 1800000000, 1000);  // saved
  // dirty again only if a newer fix came in; here the very next read has no fix
  CHECK(!lk.onRead(false, 0, 0, 0, 2000));
}

static void ageThisBootComesFromMillis() {
  LastKnownPosition lk;
  lk.onRead(true, 20.5, -103.25, 0, 10000);  // no GPS date yet
  lk.onRead(false, 0, 0, 0, 11000);
  double lat, lon;
  float age = 0;
  CHECK(lk.lookup(40000, 0, lat, lon, age));
  CHECK_NEAR(age, 30.0, 1e-3);
}

static void restoredPositionNeedsGpsTimeToBeAged() {
  LastKnownPosition lk;
  CHECK(lk.deserialize("LK1;20.5000000;-103.2500000;1800000000"));
  CHECK(lk.known());
  double lat, lon;
  float age;
  CHECK(!lk.lookup(5000, 0, lat, lon, age));  // no GPS time yet: age unknowable
  CHECK(!lk.lookup(5000, 1799999990, lat, lon, age));  // GPS time before the fix: nonsense
  CHECK(lk.lookup(5000, 1800000300, lat, lon, age));
  CHECK_NEAR(age, 300.0, 1e-3);
  LastKnownPosition noTime;
  CHECK(noTime.deserialize("LK1;20.5;-103.25;0"));
  CHECK(!noTime.lookup(5000, 1800000300, lat, lon, age));
}

static void badBlobsAreRefused() {
  LastKnownPosition lk;
  CHECK(!lk.deserialize(""));
  CHECK(!lk.deserialize("garbage"));
  CHECK(!lk.deserialize("LK1;abc;1;2"));
  CHECK(!lk.deserialize("LK1;95;1;2"));  // latitude out of range
  CHECK(!lk.deserialize("LK1;1;2;3x"));
  CHECK(!lk.known());
  CHECK(lk.deserialize("LK1"));  // nothing known yet, but well-formed
  CHECK(!lk.known());
  CHECK(LastKnownPosition().serialize() == "LK1");
}

// --- Vigilance ---

static void vigilanceFollowsThePiUntilItGoesQuiet() {
  VigilanceTracker v;
  CHECK(std::string(v.vigilance(0)) == "unknown");  // nothing since boot
  v.onPiRecord(PiSeverity::None, 1000);
  CHECK(std::string(v.vigilance(1000)) == "low");
  v.onPiRecord(PiSeverity::Critical, 2000);
  CHECK(std::string(v.vigilance(11999)) == "critical");
  CHECK(std::string(v.vigilance(12000)) == "unknown");  // PI_STALE_MS without a record
  v.onPiRecord(PiSeverity::Medium, 13000);
  CHECK(std::string(v.vigilance(13000)) == "medium");
  v.onPiRecord(PiSeverity::Unknown, 14000);  // a record with no severity
  CHECK(std::string(v.vigilance(14000)) == "unknown");
}

// --- Points and positions ---

static void pointFromACurrentFix() {
  LastKnownPosition lk;
  TrackPoint p;
  CHECK(trackPointFor(fix(), lk, 0, "low", 5000, p));
  CHECK(!p.lastKnown);
  CHECK(p.atMs == 5000);
  CHECK_NEAR(p.speedKmh, 55.0, 1e-6);
  CHECK(std::string(p.vigilance) == "low");
}

static void pointFromTheLastKnownPosition() {
  LastKnownPosition lk;
  lk.onRead(true, 20.5, -103.25, 0, 1000);
  lk.onRead(false, 0, 0, 0, 2000);
  TrackPoint p;
  CHECK(trackPointFor(GpsSnapshot(), lk, 0, "medium", 31000, p));
  CHECK(p.lastKnown);
  CHECK_NEAR(p.locationAgeS, 30.0, 1e-3);
  CHECK(p.speedKmh == 0.0f);
  CHECK_NEAR(p.lat, 20.5, 1e-9);
}

static void noPointWithoutAnyPosition() {
  LastKnownPosition lk;
  TrackPoint p;
  CHECK(!trackPointFor(GpsSnapshot(), lk, 0, "low", 1000, p));
}

static void eventPositions() {
  LastKnownPosition lk;
  EventPosition p = eventPosition(GpsSnapshot(), lk, 0, 1000);
  CHECK(!p.known);  // "position unknown"
  p = eventPosition(fix(), lk, 0, 1000);
  CHECK(p.known && !p.lastKnown && p.locationAgeS == 0.0f);
  lk.onRead(true, 21.0, -103.0, 0, 1000);
  p = eventPosition(GpsSnapshot(), lk, 0, 6000);
  CHECK(p.known && p.lastKnown);
  CHECK_NEAR(p.locationAgeS, 5.0, 1e-3);
  CHECK_NEAR(p.lat, 21.0, 1e-9);
}

static void epochConversion() {
  CHECK(epochFromUtc(2026, 10, 17, 19, 20, 12) == 1792264812ULL);
  CHECK(epochFromUtc(2024, 2, 29, 0, 0, 0) == 1709164800ULL);
  CHECK(epochFromUtc(2024, 1, 1, 0, 0, 0) == 1704067200ULL);
  CHECK(epochFromUtc(2006, 3, 1, 0, 0, 0) == 0);  // week-rollover date
  CHECK(epochFromUtc(2026, 13, 1, 0, 0, 0) == 0);
}

int main() {
  RUN(keepsTheNewestTenOldestFirst);
  RUN(aPreemptedReportsPointsGoBackFirst);
  RUN(restoreStillKeepsOnlyTheNewestTen);
  RUN(firstFixIsSavedThenEverySixtySeconds);
  RUN(losingTheFixSavesTheLastPositionOnce);
  RUN(losingTheFixRightAfterASaveDoesNotSaveAgain);
  RUN(ageThisBootComesFromMillis);
  RUN(restoredPositionNeedsGpsTimeToBeAged);
  RUN(badBlobsAreRefused);
  RUN(vigilanceFollowsThePiUntilItGoesQuiet);
  RUN(pointFromACurrentFix);
  RUN(pointFromTheLastKnownPosition);
  RUN(noPointWithoutAnyPosition);
  RUN(eventPositions);
  RUN(epochConversion);
  return report("gps_track_host_test");
}
