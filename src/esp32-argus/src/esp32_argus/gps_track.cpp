#include "gps_track.h"

#include <cstdio>
#include <cstdlib>

// --- TrackBuffer ---

void TrackBuffer::add(const TrackPoint& point) {
  if (count_ == kCapacity) {
    head_ = (head_ + 1) % kCapacity;  // drop the oldest
    count_--;
  }
  points_[(head_ + count_) % kCapacity] = point;
  count_++;
}

int TrackBuffer::take(TrackPoint* out) {
  int n = count_;
  for (int i = 0; i < n; i++) out[i] = points_[(head_ + i) % kCapacity];
  head_ = 0;
  count_ = 0;
  return n;
}

void TrackBuffer::restore(const TrackPoint* points, int count) {
  TrackPoint newer[kCapacity];
  int newerCount = take(newer);
  // Oldest first: the restored ones, then what came since; add() keeps the newest kCapacity.
  for (int i = 0; i < count; i++) add(points[i]);
  for (int i = 0; i < newerCount; i++) add(newer[i]);
}

// --- LastKnownPosition ---

bool LastKnownPosition::onRead(bool hasFix, double lat, double lon, uint64_t gpsEpochS,
                               uint32_t nowMs) {
  bool save = false;
  if (hasFix) {
    valid_ = true;
    lat_ = lat;
    lon_ = lon;
    fixEpochS_ = gpsEpochS;
    fixThisBoot_ = true;
    fixAtMs_ = nowMs;
    dirty_ = true;
    if (!everSaved_ || (uint32_t)(nowMs - lastSaveMs_) >= saveEveryMs_) save = true;
  } else if (hadFix_ && dirty_) {
    save = true;  // the fix was just lost: keep the very last position
  }
  hadFix_ = hasFix;
  if (save) {
    everSaved_ = true;
    lastSaveMs_ = nowMs;
    dirty_ = false;
  }
  return save;
}

bool LastKnownPosition::lookup(uint32_t nowMs, uint64_t gpsEpochNowS, double& lat, double& lon,
                               float& ageS) const {
  if (!valid_) return false;
  if (fixThisBoot_) {
    ageS = (float)(uint32_t)(nowMs - fixAtMs_) / 1000.0f;
  } else if (fixEpochS_ > 0 && gpsEpochNowS >= fixEpochS_) {
    ageS = (float)(gpsEpochNowS - fixEpochS_);
  } else {
    return false;  // from an earlier boot, and no GPS time to age it by
  }
  lat = lat_;
  lon = lon_;
  return true;
}

std::string LastKnownPosition::serialize() const {
  if (!valid_) return "LK1";
  char buf[96];
  std::snprintf(buf, sizeof(buf), "LK1;%.7f;%.7f;%llu", lat_, lon_,
                (unsigned long long)fixEpochS_);
  return buf;
}

bool LastKnownPosition::deserialize(const std::string& blob) {
  *this = LastKnownPosition(saveEveryMs_);
  if (blob == "LK1") return true;  // saved with nothing known yet
  if (blob.compare(0, 4, "LK1;") != 0) return false;
  const char* p = blob.c_str() + 4;
  char* end = nullptr;
  double lat = std::strtod(p, &end);
  if (end == p || *end != ';') return false;
  p = end + 1;
  double lon = std::strtod(p, &end);
  if (end == p || *end != ';') return false;
  p = end + 1;
  unsigned long long epoch = std::strtoull(p, &end, 10);
  if (end == p || *end != '\0') return false;
  if (lat < -90.0 || lat > 90.0 || lon < -180.0 || lon > 180.0) return false;
  valid_ = true;
  lat_ = lat;
  lon_ = lon;
  fixEpochS_ = epoch;
  everSaved_ = true;  // this copy is in NVS already
  return true;
}

// --- VigilanceTracker ---

void VigilanceTracker::onPiRecord(PiSeverity severity, uint32_t nowMs) {
  seen_ = true;
  atMs_ = nowMs;
  latest_ = severity;
}

const char* VigilanceTracker::vigilance(uint32_t nowMs) const {
  if (!seen_ || (uint32_t)(nowMs - atMs_) >= staleMs_) return "unknown";
  return backendVigilance(latest_);
}

// --- Points and event positions ---

bool trackPointFor(const GpsSnapshot& gps, const LastKnownPosition& lastKnown, uint64_t gpsEpochS,
                   const char* vigilance, uint32_t nowMs, TrackPoint& out) {
  out = TrackPoint();
  out.atMs = nowMs;
  out.vigilance = vigilance;
  if (gps.hasFix) {
    out.lat = gps.lat;
    out.lon = gps.lon;
    out.speedKmh = gps.speedKmh;
    return true;
  }
  float ageS = 0.0f;
  if (!lastKnown.lookup(nowMs, gpsEpochS, out.lat, out.lon, ageS)) return false;
  out.lastKnown = true;
  out.locationAgeS = ageS;
  out.speedKmh = 0.0f;
  return true;
}

EventPosition eventPosition(const GpsSnapshot& gps, const LastKnownPosition& lastKnown,
                            uint64_t gpsEpochS, uint32_t nowMs) {
  EventPosition p;
  if (gps.hasFix) {
    p.known = true;
    p.lat = gps.lat;
    p.lon = gps.lon;
    return p;
  }
  float ageS = 0.0f;
  if (lastKnown.lookup(nowMs, gpsEpochS, p.lat, p.lon, ageS)) {
    p.known = true;
    p.lastKnown = true;
    p.locationAgeS = ageS;
  }
  return p;
}

uint64_t epochFromUtc(int year, int month, int day, int hour, int minute, int second) {
  if (year < 2024 || month < 1 || month > 12 || day < 1 || day > 31) return 0;
  // Days from civil (Howard Hinnant's algorithm), no timegm() needed.
  int y = year - (month <= 2 ? 1 : 0);
  int era = y / 400;
  int yoe = y - era * 400;
  int mp = (month + 9) % 12;
  int doy = (153 * mp + 2) / 5 + day - 1;
  int doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  long long days = (long long)era * 146097 + doe - 719468;
  return (uint64_t)(days * 86400LL + hour * 3600LL + minute * 60LL + second);
}
