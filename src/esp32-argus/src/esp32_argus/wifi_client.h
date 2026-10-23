#pragma once

#include <Arduino.h>

// The fallback data transport to backend-argus (mobile data is tried first when the Pi
// configures an APN, see backend_api.h). The network to join comes from the Pi over Bluetooth
// (CONFIG, credentials.h), not from config.h: no SSID from the Pi = WiFi stays off.
//
// wifiBegin() runs in setup(); after that only the sender task calls into this module (it owns
// every transport to the backend).

// Call after credentialsBegin(): joins the NVS-cached network, if any (blocks up to
// WIFI_CONNECT_TIMEOUT_MS).
void wifiBegin();

// Call periodically from the sender task -- reconnects if the link drops, and switches networks
// when the Pi hands over new WiFi settings, without ever blocking. Retries are rate-limited to
// WIFI_RETRY_INTERVAL_MS.
void wifiEnsureConnected();

bool wifiIsConnected();
bool wifiConfigured();  // the Pi gave this device a WiFi network to join
