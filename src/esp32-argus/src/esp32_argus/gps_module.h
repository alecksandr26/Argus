#pragma once

#include <Arduino.h>

// Wraps the NEO-6M GPS module (TinyGPSPlus over a dedicated HardwareSerial) -- this device's own
// GPS reading: the points of every report and the position of every event (C2.1). cv-argus never
// populates geolocation itself; the Pi has no GPS in this design.
//
// Only the sketch's gpsTask calls into this module (TinyGPSPlus isn't thread-safe): it drains the
// UART continuously and reads a point every GPS_READ_INTERVAL_MS (gps_track.h does the rest).

void gpsBegin();

// Feed the parser -- call this as often as possible, it just drains whatever bytes are waiting
// on the GPS UART.
void gpsPoll();

// A *current* fix: the position was updated in the last GPS_FIX_MAX_AGE_MS (TinyGPSPlus alone
// keeps the first fix "valid" forever). BENCH_FAKE_GPS: always, at BENCH_GPS_LAT/LON.
bool gpsHasFix();
double gpsLatitude();   // degrees, only valid if gpsHasFix()
double gpsLongitude();  // degrees, only valid if gpsHasFix()
double gpsSpeedKmh();   // GPS-derived speed (0 when stale) -- NOT a CAN-bus/OBD reading
uint32_t gpsSatellites();  // used in the fix (GGA): always 0 while there's no fix

// The GPS's UTC time now, as epoch seconds, or 0 when it has none (no date/time yet, the last
// time sentence more than 5 s old, or a date before 2024). This is what ages the last known
// position across a reboot (gps_track.h LastKnownPosition).
uint64_t gpsEpochSeconds();

// One Serial line with the raw NEO-6M state, ignoring BENCH_FAKE_GPS: bytes received (0 means
// a wiring/baud problem, not a sky problem), corrupted sentences, satellites used, satellites
// in view / actually heard (SNR > 0) / best SNR from the last GSV cycle, NEO-6M power-ups seen
// (rising after boot = the module keeps resetting), fix, position. For bench debugging.
void gpsLogStatus();
