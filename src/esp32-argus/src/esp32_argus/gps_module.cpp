#include "gps_module.h"

#include <TinyGPSPlus.h>

#include "config.h"
#include "gps_track.h"

namespace {
HardwareSerial gpsSerial(2);
TinyGPSPlus gps;

// gps.satellites is GGA's "satellites used in the fix", which stays 0 until there is a fix,
// so it can't tell "the antenna hears nothing" from "it hears satellites but is still
// downloading their orbits". GSV lists the satellites in view with their signal strength
// (SNR, dB-Hz), 4 per sentence, spread over several sentences.
TinyGPSCustom gsvTotalMsgs(gps, "GPGSV", 1);
TinyGPSCustom gsvMsgNum(gps, "GPGSV", 2);
TinyGPSCustom gsvInView(gps, "GPGSV", 3);
TinyGPSCustom gsvSnr0(gps, "GPGSV", 7);
TinyGPSCustom gsvSnr1(gps, "GPGSV", 11);
TinyGPSCustom gsvSnr2(gps, "GPGSV", 15);
TinyGPSCustom gsvSnr3(gps, "GPGSV", 19);
TinyGPSCustom* const gsvSnr[4] = {&gsvSnr0, &gsvSnr1, &gsvSnr2, &gsvSnr3};
// The NEO-6M prints "$GPTXT,01,01,02,u-blox ag - www.u-blox.com" every time it powers up, so
// counting it shows the module rebooting (e.g. browning out on the SIM800L's 2 A bursts).
TinyGPSCustom txtText(gps, "GPTXT", 4);

// Summed over one GSV cycle, then published when the cycle's last sentence arrives.
int cycleHeard = 0;
int cycleBestSnr = 0;
int satsInView = 0;
int satsHeard = 0;  // satellites with a nonzero SNR, i.e. actually being received
int bestSnr = 0;
uint32_t gpsBoots = 0;

void onGsv() {
  int total = atoi(gsvTotalMsgs.value());
  int msgNum = atoi(gsvMsgNum.value());
  int inView = atoi(gsvInView.value());
  if (msgNum == 1) cycleHeard = cycleBestSnr = 0;
  // The last sentence of a cycle carries fewer than 4 satellites, and TinyGPSCustom keeps the
  // previous sentence's value for a field that's missing, so only read the slots this one has.
  int slots = constrain(inView - (msgNum - 1) * 4, 0, 4);
  for (int i = 0; i < slots; i++) {
    int snr = atoi(gsvSnr[i]->value());
    if (snr > 0) {
      cycleHeard++;
      if (snr > cycleBestSnr) cycleBestSnr = snr;
    }
  }
  if (msgNum == total) {
    satsInView = inView;
    satsHeard = cycleHeard;
    bestSnr = cycleBestSnr;
  }
}
}  // namespace

void gpsBegin() {
  // gpsTask drains this every ~20 ms now, but the NEO-6M keeps sending ~500 bytes/s whatever
  // else the core is doing. The default 256-byte RX buffer overflowed and corrupted sentences
  // when the loop also ran the Bluetooth pulls (see failedChecksum in gpsLogStatus()); 4 KB
  // covers several seconds of any stall.
  gpsSerial.setRxBufferSize(4096);
  gpsSerial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX, GPS_TX);
}

void gpsPoll() {
  while (gpsSerial.available()) {
    if (!gps.encode(gpsSerial.read())) continue;  // true once a whole valid sentence is in
    if (gsvMsgNum.isUpdated()) onGsv();
    if (txtText.isUpdated() && strncmp(txtText.value(), "u-blox ag", 9) == 0) gpsBoots++;
  }
}

// TinyGPSPlus keeps isValid() true forever after the first fix, so a fix is "current" only while
// its last update is recent: without this, a lost fix would keep reporting the old position as
// live instead of the last-known one.
bool gpsHasFix() {
  return BENCH_FAKE_GPS || (gps.location.isValid() && gps.location.age() < GPS_FIX_MAX_AGE_MS);
}
double gpsLatitude() { return BENCH_FAKE_GPS ? BENCH_GPS_LAT : gps.location.lat(); }
double gpsLongitude() { return BENCH_FAKE_GPS ? BENCH_GPS_LON : gps.location.lng(); }
double gpsSpeedKmh() {
  return gps.speed.isValid() && gps.speed.age() < GPS_FIX_MAX_AGE_MS ? gps.speed.kmph() : 0.0;
}
uint32_t gpsSatellites() { return gps.satellites.isValid() ? gps.satellites.value() : 0; }

void gpsLogStatus() {
  Serial.print("[gps] chars=");
  Serial.print(gps.charsProcessed());
  Serial.print(" bad=");
  Serial.print(gps.failedChecksum());
  Serial.print(" sats=");
  Serial.print(gpsSatellites());
  Serial.print(" inView=");
  Serial.print(satsInView);
  Serial.print(" heard=");
  Serial.print(satsHeard);
  Serial.print(" bestSnr=");
  Serial.print(bestSnr);
  Serial.print(" boots=");
  Serial.print(gpsBoots);
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

uint64_t gpsEpochSeconds() {
  if (!gps.date.isValid() || !gps.time.isValid()) return 0;
  // A stale reading (GPS time lost minutes ago) or a week-number-rollover date (1999/2006) is no
  // time at all; epochFromUtc() refuses dates before 2024.
  uint32_t age = gps.time.age();
  if (age > 5000) return 0;
  uint64_t epoch = epochFromUtc(gps.date.year(), gps.date.month(), gps.date.day(),
                                gps.time.hour(), gps.time.minute(), gps.time.second());
  return epoch == 0 ? 0 : epoch + age / 1000;
}
