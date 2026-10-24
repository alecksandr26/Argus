#include "wifi_client.h"

#include <WiFi.h>

#include "config.h"
#include "credentials.h"

namespace {
unsigned long lastRetryAt = 0;
// This module's copy of the Pi's settings, refreshed when they change (credentials.h).
DeviceCredentials creds;
uint32_t credsGeneration = 0;
// The network the radio was last told to join -- compared against the Pi's settings so a new
// SSID/password in its .env reaches this device without a reflash.
String targetSsid;
String targetPassword;
// Whether the WiFi driver is running. It starts off and only runs while WiFi is needed: the
// driver holds tens of KB of heap, which classic Bluetooth needs (an exhausted heap aborts in
// the Bluetooth stack, assert "fixed_queue_new ... lock != NULL").
bool radioOn = false;

void refreshCredentials() { credentialsCopyIfChanged(creds, credsGeneration); }

void startConnect() {
  targetSsid = creds.wifiSsid;
  targetPassword = creds.wifiPassword;
  WiFi.disconnect();
  if (targetSsid.length() == 0) return;
  WiFi.begin(targetSsid.c_str(), targetPassword.c_str());
}

void radioUp(const char* why) {
  Serial.printf("[wifi] Turning WiFi on (%s) -- heap free %u B before\n", why,
                (unsigned)ESP.getFreeHeap());
  WiFi.mode(WIFI_STA);
  radioOn = true;
  lastRetryAt = millis();
  startConnect();
}

void radioDown(const char* why) {
  WiFi.disconnect(/*wifioff=*/true);
  WiFi.mode(WIFI_OFF);
  radioOn = false;
  targetSsid = "";
  targetPassword = "";
  Serial.printf("[wifi] WiFi off (%s) -- heap free %u B after\n", why,
                (unsigned)ESP.getFreeHeap());
}
}  // namespace

void wifiBegin() {
  refreshCredentials();
  if (creds.wifiSsid.length() == 0) {
    Serial.println("[wifi] No WiFi configured -- radio off (mobile data only)");
  } else if (creds.useGprs()) {
    Serial.print("[wifi] \"");
    Serial.print(creds.wifiSsid);
    Serial.println("\" is the fallback -- radio off until mobile data fails");
  } else {
    Serial.println("[wifi] No mobile data APN -- WiFi is the only path, the sender starts it");
  }
}

void wifiEnsureConnected(bool wanted) {
  refreshCredentials();
  if (!wanted || creds.wifiSsid.length() == 0) {
    if (radioOn) radioDown(creds.wifiSsid.length() == 0 ? "the Pi no longer configures WiFi"
                                                        : "mobile data works again");
    return;
  }
  if (!radioOn) {
    radioUp(creds.useGprs() ? "mobile data failed" : "no mobile data APN");
    return;
  }

  unsigned long now = millis();
  if (creds.wifiSsid != targetSsid || creds.wifiPassword != targetPassword) {
    Serial.println("[wifi] New WiFi settings from the Pi -- reconnecting");
    startConnect();
    lastRetryAt = now;
    return;
  }
  if (WiFi.status() == WL_CONNECTED) return;
  if (now - lastRetryAt < WIFI_RETRY_INTERVAL_MS) return;
  lastRetryAt = now;

  Serial.println("[wifi] Reconnecting...");
  startConnect();
}

bool wifiIsConnected() { return radioOn && WiFi.status() == WL_CONNECTED; }

bool wifiIsOn() { return radioOn; }
