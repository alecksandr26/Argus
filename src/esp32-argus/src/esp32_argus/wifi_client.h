#pragma once

#include <Arduino.h>

// The fallback data transport to backend-argus (mobile data is tried first when the Pi
// configures an APN, see backend_api.h), and the only one for PUT. The network to join comes
// from the Pi over Bluetooth (CONFIG, credentials.h), not from config.h: no SSID from the Pi =
// WiFi stays off.

// Call after credentialsBegin(): joins the NVS-cached network, if any (blocks up to
// WIFI_CONNECT_TIMEOUT_MS).
void wifiBegin();

// Call periodically (once per loop() is fine) -- reconnects if the link drops, and switches
// networks when the Pi hands over new WiFi settings, without ever blocking: a disconnected
// WiFi radio must not stall GPS polling or the panic button. Retries are rate-limited to
// WIFI_RETRY_INTERVAL_MS.
void wifiEnsureConnected();

bool wifiIsConnected();
