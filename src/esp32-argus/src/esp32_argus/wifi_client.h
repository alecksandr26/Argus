#pragma once

#include <Arduino.h>

// The fallback data transport to backend-argus (mobile data is tried first when the Pi
// configures an APN, see backend_api.h). The network to join comes from the Pi over Bluetooth
// (CONFIG, credentials.h), not from config.h: no SSID from the Pi = WiFi stays off.
//
// The radio is OFF by default and only runs while it is needed: when the Pi gives no mobile data
// APN (WiFi is then the only path), or while mobile data is failing. When mobile data works again
// the radio is turned off, which frees the WiFi driver's heap for classic Bluetooth.
//
// wifiBegin() runs in setup() and never starts the radio; after that only the sender task calls
// into this module (it owns every transport to the backend).

// Call after credentialsBegin(): logs which role WiFi has. Doesn't start the radio.
void wifiBegin();

// Call periodically from the sender task. `wanted` = WiFi is needed now (backend_api decides):
// starts or stops the radio accordingly, reconnects if the link drops, and switches networks when
// the Pi hands over new WiFi settings, without ever blocking. Retries are rate-limited to
// WIFI_RETRY_INTERVAL_MS.
void wifiEnsureConnected(bool wanted);

bool wifiIsConnected();
bool wifiIsOn();  // the radio is running (it may still be joining)
