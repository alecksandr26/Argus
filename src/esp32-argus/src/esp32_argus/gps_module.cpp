#include "gps_module.h"

#include <TinyGPSPlus.h>

#include "config.h"

namespace {
HardwareSerial gpsSerial(2);
TinyGPSPlus gps;
}  // namespace

void gpsBegin() {
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

String gpsMapsLink() {
  String link = "https://www.google.com/maps?q=";
  link += String(gpsLatitude(), 6);
  link += ",";
  link += String(gpsLongitude(), 6);
  return link;
}

String gpsUtcIso8601() {
  if (!gps.date.isValid() || !gps.time.isValid()) return String();
  char buf[25];
  snprintf(buf, sizeof(buf), "%04d-%02d-%02dT%02d:%02d:%02dZ", gps.date.year(),
           gps.date.month(), gps.date.day(), gps.time.hour(), gps.time.minute(),
           gps.time.second());
  return String(buf);
}
