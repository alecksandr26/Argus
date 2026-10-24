// Mobile data (SIM800L GPRS) first, Wi-Fi as the fallback (user decision). Only a *transport*
// failure switches paths; a server answer (even 4xx/5xx) proves the path works. Once on Wi-Fi,
// GPRS is retried after a hold-off, not on every request (no flapping, NET-04).
// SOURCES: transport_policy.cpp
#include <string>

#include "check.h"
#include "transport_policy.h"

static void startsOnGprs() {
  TransportSelector t(60000);
  CHECK(t.pick(0) == Transport::Gprs);
}

static void gprsTransportFailureFallsBackToWifi() {
  TransportSelector t(60000);
  t.report(Transport::Gprs, /*transportOk=*/false, 1000);
  CHECK(t.pick(1001) == Transport::Wifi);
}

static void serverErrorOnGprsDoesNotFallBack() {
  TransportSelector t(60000);
  t.report(Transport::Gprs, /*transportOk=*/true, 1000);  // got a 422/500 back
  CHECK(t.pick(1001) == Transport::Gprs);
}

static void gprsRetriedOnlyAfterHoldOff() {
  TransportSelector t(60000);
  t.report(Transport::Gprs, false, 1000);
  CHECK(t.pick(30000) == Transport::Wifi);
  CHECK(t.pick(61001) == Transport::Gprs);
}

static void failedRetryExtendsHoldOff() {
  TransportSelector t(60000);
  t.report(Transport::Gprs, false, 1000);
  t.report(Transport::Gprs, false, 61001);  // the retry also failed
  CHECK(t.pick(70000) == Transport::Wifi);
}

static void holdOffNeedsWifiActuallyUp() {
  // A truck with mobile data only: a GPRS failure must not park it on a WiFi it doesn't have.
  TransportSelector t(60000);
  t.report(Transport::Gprs, false, 1000);
  CHECK(t.pick(2000, /*wifiUp=*/false) == Transport::Gprs);
  CHECK(t.pick(2000, /*wifiUp=*/true) == Transport::Wifi);
  CHECK(t.inHoldOff(2000));
  CHECK(!t.inHoldOff(61001));
}

static void gprsFailingKeepsWifiFallbackUntilGprsWorks() {
  // The WiFi radio runs only while this holds (backend_api.cpp's backendPoll): from a mobile-data
  // failure until a mobile request works again -- past the end of the hold-off too.
  TransportSelector t(60000);
  CHECK(!t.gprsFailing());  // boot: mobile data first, WiFi off
  t.report(Transport::Gprs, false, 1000);
  CHECK(t.gprsFailing());
  CHECK(!t.inHoldOff(61001));
  CHECK(t.gprsFailing());  // hold-off over, but mobile data hasn't worked yet
  t.report(Transport::Wifi, true, 62000);  // WiFi results don't count
  CHECK(t.gprsFailing());
  t.report(Transport::Gprs, true, 63000);  // a mobile request worked: WiFi can go off
  CHECK(!t.gprsFailing());
}

static void deadlineCapsEachStep() {
  Deadline d(1000, 8000);
  CHECK(!d.expired(1000));
  CHECK(d.remaining(1000) == 8000);
  CHECK(d.cap(1000, 2000) == 2000);  // the step's own timeout
  CHECK(d.cap(8000, 2000) == 1000);  // never past the deadline
  CHECK(d.remaining(8999) == 1);
  CHECK(d.expired(9000));
  CHECK(d.remaining(9000) == 0);
  CHECK(d.cap(12000, 2000) == 0);
  Deadline w(0xFFFFF000u, 8000);  // across the millis() wrap
  CHECK(!w.expired(0x00000F00u));
  CHECK(w.remaining(0x00000F00u) == 8000 - 0x1F00u);
  CHECK(w.expired(0x00001000u + 4000u));
}

static void holdOffSurvivesMillisWrap() {
  TransportSelector t(60000);
  t.report(Transport::Gprs, false, 0xFFFFF000u);
  CHECK(t.pick(0x00001000u) == Transport::Wifi);  // only ~8 s later across the wrap
}

// The real deadline per attempt (final-tweaks contracts C4).
static void attemptTimeouts() {
  AttemptTimeouts t;
  CHECK(attemptTimeoutMs(t, Transport::Wifi, false, false) == 8000);
  CHECK(attemptTimeoutMs(t, Transport::Wifi, true, true) == 8000);  // no modem on WiFi
  CHECK(attemptTimeoutMs(t, Transport::Gprs, false, false) == 8000);
  CHECK(attemptTimeoutMs(t, Transport::Gprs, true, false) == 15000);
  CHECK(attemptTimeoutMs(t, Transport::Gprs, false, true) == 18000);  // + modem setup
  CHECK(attemptTimeoutMs(t, Transport::Gprs, true, true) == 25000);
  CHECK(std::string(transportName(Transport::Gprs)) == "gprs");
  CHECK(std::string(transportName(Transport::Wifi)) == "wifi");
}

int main() {
  RUN(attemptTimeouts);
  RUN(startsOnGprs);
  RUN(gprsTransportFailureFallsBackToWifi);
  RUN(serverErrorOnGprsDoesNotFallBack);
  RUN(gprsRetriedOnlyAfterHoldOff);
  RUN(failedRetryExtendsHoldOff);
  RUN(holdOffSurvivesMillisWrap);
  RUN(holdOffNeedsWifiActuallyUp);
  RUN(gprsFailingKeepsWifiFallbackUntilGprsWorks);
  RUN(deadlineCapsEachStep);
  return report("transport_policy_host_test");
}
