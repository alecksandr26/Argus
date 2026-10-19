#pragma once

#include "device_config.h"

// The backend credentials this device is currently using -- plus its WiFi and mobile-data
// settings, which come along in the same CONFIG reply -- cached in NVS (Preferences) so a
// reboot while the Pi is still booting doesn't leave the truck unable to reach the backend.
// wifi_client.cpp and gprs_http.cpp follow changes on their own (new SSID -> reconnect, new
// APN -> reopen the bearer).
//
// The Pi is the source of truth (its argus-device.env, issued from the dashboard), so the cache
// starts out *stale* on every boot and is re-fetched over Bluetooth (CONFIG) before the next
// pull -- that's how a key rotated in the dashboard reaches this device without a reflash. A 401
// from the backend marks it stale again (backend_api.cpp).

void credentialsBegin();  // loads the NVS copy, if any; marks the cache stale

// True once there's a usable copy (from NVS or from the Pi).
bool credentialsReady();
const DeviceCredentials& credentials();

// True when the next Bluetooth poll should ask the Pi for CONFIG first.
bool credentialsStale();
void credentialsMarkStale();

// Stores a fresh copy from the Pi (writes NVS only if it changed) and clears the stale flag.
void credentialsApply(const DeviceCredentials& fresh);

// The Pi answered NOCONFIG: nothing is provisioned there. Clears the stale flag so we don't ask
// every poll; keeps any NVS copy (still the best guess) until the Pi has one again.
void credentialsNoneOnPi();
