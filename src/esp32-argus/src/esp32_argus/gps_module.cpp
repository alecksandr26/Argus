#include "gps_module.h"

#include <TinyGPSPlus.h>

#include "config.h"

namespace {
HardwareSerial gpsSerial(2);
TinyGPSPlus gps;
}  // namespace

void gpsBegin() {
  // gpsPoll() only runs between Bluetooth pulls, which can block for seconds while the NEO-6M
  // keeps sending ~500 bytes/s. The default 256-byte RX buffer overflows and corrupts
  // sentences (see failedChecksum in gpsLogStatus()); 4 KB covers several seconds.
  gpsSerial.setRxBufferSize(4096);
  gpsSerial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX, GPS_TX);
}

void gpsPoll() {
  while (gpsSerial.available()) {
    gps.encode(gpsSerial.read());
  }
}

bool gpsHasFix() { return BENCH_FAKE_GPS || gps.location.isValid(); }
double gpsLatitude() { return BENCH_FAKE_GPS ? BENCH_GPS_LAT : gps.location.lat(); }
double gpsLongitude() { return BENCH_FAKE_GPS ? BENCH_GPS_LON : gps.location.lng(); }
double gpsSpeedKmh() { return gps.speed.isValid() ? gps.speed.kmph() : 0.0; }
uint32_t gpsSatellites() { return gps.satellites.isValid() ? gps.satellites.value() : 0; }

void gpsLogStatus() {
  Serial.print("[gps] chars=");
  Serial.print(gps.charsProcessed());
  Serial.print(" bad=");
  Serial.print(gps.failedChecksum());
  Serial.print(" sats=");
  Serial.print(gpsSatellites());
  if (gps.location.isValid()) {
    Serial.print(" fix=yes lat=");
    Serial.print(gps.location.lat(), 6);
    Serial.print(" lon=");
    Serial.print(gps.location.lng(), 6);
  } else {
    Serial.print(" fix=no");
  }
  if (BENCH_FAKE_GPS) Serial.print(" (BENCH_FAKE_GPS on: relaying the fake position)");
  Serial.println();
}

String gpsMapsLink() {
  String link = "https://www.google.com/maps?q=";
  link += String(gpsLatitude(), 6);
  link += ",";
  link += String(gpsLongitude(), 6);
  return link;
}

String gpsUtcIso8601() {
  if (!gps.date.isValid() || !gps.time.isValid()) return String();
  // A stale reading (GPS signal lost minutes ago) or a week-number-rollover date (1999/2006)
  // would be rejected by the backend's occurred_at window with a 422 -- report "no time".
  if (gps.time.age() > 5000 || gps.date.year() < 2024) return String();
  char buf[25];
  snprintf(buf, sizeof(buf), "%04d-%02d-%02dT%02d:%02d:%02dZ", gps.date.year(),
           gps.date.month(), gps.date.day(), gps.time.hour(), gps.time.minute(),
           gps.time.second());
  return String(buf);
}
