#pragma once

#include <Arduino.h>

// This device's main data transport -- carries both the HTTP calls to backend-argus
// (backend_api.h) and, indirectly, the periodic Bluetooth pull from cv-argus (the pull
// itself is over Bluetooth, but a pulled record is worthless until it's relayed over this
// link -- see esp32_argus.ino's handlePiRecord()).

void wifiBegin();

// Call periodically (once per loop() is fine) -- reconnects if the link drops, without
// ever blocking: a disconnected WiFi radio must not stall GPS polling or the panic
// button. Rate-limited internally to WIFI_RETRY_INTERVAL_MS between attempts.
void wifiEnsureConnected();

bool wifiIsConnected();
