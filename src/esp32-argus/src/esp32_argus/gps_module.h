#pragma once

#include <Arduino.h>

// Wraps the NEO-6M GPS module (TinyGPSPlus over a dedicated HardwareSerial) -- this
// device's own GPS reading, attached to every record relayed to backend-argus (see
// src/esp32-argus/README.md section 3: cv-argus never populates geolocation itself, the
// Pi has no GPS in this design).

void gpsBegin();

// Feed the parser -- call this as often as possible from loop(), it just drains whatever
// bytes are waiting on the GPS UART.
void gpsPoll();

bool gpsHasFix();
double gpsLatitude();   // degrees, only valid if gpsHasFix()
double gpsLongitude();  // degrees, only valid if gpsHasFix()
double gpsSpeedKmh();   // GPS-derived speed -- NOT a CAN-bus/OBD reading, see config.h
uint32_t gpsSatellites();

// One Serial line with the raw NEO-6M state, ignoring BENCH_FAKE_GPS: bytes received (0 means
// a wiring/baud problem, not a sky problem), corrupted sentences, satellites, fix, position. For bench debugging.
void gpsLogStatus();

String gpsMapsLink();  // "https://www.google.com/maps?q=<lat>,<lon>" for the panic SMS

// UTC date/time from the GPS fix itself, formatted as "YYYY-MM-DDTHH:MM:SSZ" -- used for
// Alert.resolved_at (see backend_api.h) so this device doesn't need an NTP-synced clock
// just to say "now" in UTC. Returns "" if the GPS has no valid date/time yet.
String gpsUtcIso8601();
