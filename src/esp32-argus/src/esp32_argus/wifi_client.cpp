#include "wifi_client.h"

#include <WiFi.h>

#include "config.h"

namespace {
unsigned long lastRetryAt = 0;
}  // namespace

void wifiBegin() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  Serial.print("[wifi] Connecting");
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
  if (WiFi.status() == WL_CONNECTED) return;

  unsigned long now = millis();
  if (now - lastRetryAt < WIFI_RETRY_INTERVAL_MS) return;
  lastRetryAt = now;

  Serial.println("[wifi] Reconnecting...");
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

bool wifiIsConnected() { return WiFi.status() == WL_CONNECTED; }
