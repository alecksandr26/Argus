#include "wifi_client.h"

#include <WiFi.h>

#include "config.h"
#include "credentials.h"

namespace {
unsigned long lastRetryAt = 0;
// The network the radio was last told to join -- compared against the Pi's settings so a new
// SSID/password in its .env reaches this device without a reflash.
String targetSsid;
String targetPassword;

void startConnect() {
  targetSsid = credentials().wifiSsid;
  targetPassword = credentials().wifiPassword;
  WiFi.disconnect();
  if (targetSsid.length() == 0) return;
  WiFi.begin(targetSsid.c_str(), targetPassword.c_str());
}
}  // namespace

void wifiBegin() {
  WiFi.mode(WIFI_STA);
  lastRetryAt = millis();
  if (credentials().wifiSsid.length() == 0) {
    Serial.println("[wifi] No WiFi configured yet -- waiting for the Pi's CONFIG");
    return;
  }
  startConnect();

  Serial.print("[wifi] Connecting to \"");
  Serial.print(targetSsid);
  Serial.print("\"");
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < WIFI_CONNECT_TIMEOUT_MS) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("[wifi] Connected, IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("[wifi] Not connected yet -- will keep retrying from the main loop");
  }
  lastRetryAt = millis();
}

void wifiEnsureConnected() {
  unsigned long now = millis();
  if (credentials().wifiSsid != targetSsid || credentials().wifiPassword != targetPassword) {
    Serial.println(credentials().wifiSsid.length() > 0
                       ? "[wifi] New WiFi settings from the Pi -- reconnecting"
                       : "[wifi] The Pi no longer configures WiFi -- disconnecting");
    startConnect();
    lastRetryAt = now;
    return;
  }
  if (targetSsid.length() == 0 || WiFi.status() == WL_CONNECTED) return;

  if (now - lastRetryAt < WIFI_RETRY_INTERVAL_MS) return;
  lastRetryAt = now;

  Serial.println("[wifi] Reconnecting...");
  startConnect();
}

bool wifiIsConnected() { return WiFi.status() == WL_CONNECTED; }
