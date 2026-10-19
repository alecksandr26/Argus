// When the main loop may restart a Wi-Fi connection attempt (NET-13).
// Today wifiEnsureConnected() calls WiFi.disconnect() + WiFi.begin() every
// WIFI_RETRY_INTERVAL_MS (10 s) while not connected, even if an attempt is still associating or
// waiting on DHCP. An access point that takes longer than 10 s is restarted before it can
// finish, forever. The rule pinned here: an attempt in progress gets WIFI_CONNECT_TIMEOUT_MS
// (20 s, what setup() already allows); a failed or lost link is retried every
// WIFI_RETRY_INTERVAL_MS; a connected link is never restarted. wifi_retry.{h,cpp} is that rule,
// extracted from wifi_client.cpp so it can run here.
// SOURCES: wifi_retry.cpp
#include "check.h"
#include "wifi_retry.h"

static const uint32_t CONNECT_TIMEOUT = 20000;
static const uint32_t RETRY_INTERVAL = 10000;

static void attemptInProgressIsNotRestartedBeforeTimeout() {
  WifiRetry w(CONNECT_TIMEOUT, RETRY_INTERVAL);
  w.begun(0);
  CHECK(!w.shouldRestart(10000, WifiLink::Connecting));
  CHECK(!w.shouldRestart(19999, WifiLink::Connecting));
  CHECK(w.shouldRestart(20000, WifiLink::Connecting));
}

static void failedAttemptIsRetriedAfterTheInterval() {
  WifiRetry w(CONNECT_TIMEOUT, RETRY_INTERVAL);
  w.begun(0);
  CHECK(!w.shouldRestart(9999, WifiLink::Failed));
  CHECK(w.shouldRestart(10000, WifiLink::Failed));
}

static void connectedLinkIsNeverRestarted() {
  WifiRetry w(CONNECT_TIMEOUT, RETRY_INTERVAL);
  w.begun(0);
  CHECK(!w.shouldRestart(60000, WifiLink::Connected));
}

static void restartResetsTheTimer() {
  WifiRetry w(CONNECT_TIMEOUT, RETRY_INTERVAL);
  w.begun(0);
  CHECK(w.shouldRestart(20000, WifiLink::Connecting));
  w.begun(20000);
  CHECK(!w.shouldRestart(30000, WifiLink::Connecting));
  CHECK(w.shouldRestart(40000, WifiLink::Connecting));
}

static void survivesMillisWrap() {
  WifiRetry w(CONNECT_TIMEOUT, RETRY_INTERVAL);
  w.begun(0xFFFFF000u);  // ~4 s before millis() wraps
  CHECK(!w.shouldRestart(0x00002000u, WifiLink::Connecting));  // ~12 s later
  CHECK(w.shouldRestart(0x00004000u + 8000u, WifiLink::Connecting));  // ~28 s later
}

int main() {
  RUN(attemptInProgressIsNotRestartedBeforeTimeout);
  RUN(failedAttemptIsRetriedAfterTheInterval);
  RUN(connectedLinkIsNeverRestarted);
  RUN(restartResetsTheTimer);
  RUN(survivesMillisWrap);
  return report("wifi_retry_host_test");
}
