#pragma once

#include <stdint.h>

#include "device_config.h"

// The backend credentials this device is currently using -- plus its WiFi and mobile-data
// settings, which come along in the same CONFIG reply -- cached in NVS (Preferences) so a
// reboot while the Pi is still booting doesn't leave the truck unable to reach the backend.
// The truck plate and the SMS alert phone come in the same reply but are kept in RAM only
// (grip-alarm contracts C1.4: the phone is personal data): after a reboot there is no SMS until
// the next CONFIG, which the firmware fetches at every boot.
// wifi_client.cpp, gprs_http.cpp and backend_api.cpp follow changes on their own (new SSID ->
// reconnect, new APN -> reopen the bearer, new key/URL -> used by the next report).
//
// The Pi is the source of truth (its argus-device.env, issued from the dashboard), so the cache
// starts out *stale* on every boot and is re-fetched over Bluetooth (CONFIG) before the next
// pull -- that's how a key rotated in the dashboard reaches this device without a reflash. A 401
// from the backend marks it stale again (the sender task).
//
// Thread-safe: the loop task applies a fresh CONFIG while the sender task reads the credentials,
// so every call here takes this module's own mutex, and readers get a copy, never a reference.

void credentialsBegin();  // loads the NVS copy, if any; marks the cache stale. Before any task.

// True once there's a usable copy (from NVS or from the Pi).
bool credentialsReady();

// Bumped on every change; a reader keeps the generation it copied and calls
// credentialsCopyIfChanged() each time it needs them (no copy, no heap churn, when unchanged).
uint32_t credentialsGeneration();
DeviceCredentials credentialsCopy();
bool credentialsCopyIfChanged(DeviceCredentials& out, uint32_t& generation);

// True when the next Bluetooth poll should ask the Pi for CONFIG first.
bool credentialsStale();
void credentialsMarkStale();

// Stores a fresh copy from the Pi (writes NVS only if a cached field changed; the plate and phone
// never go there) and clears the stale flag. True when the backend identity changed (truck id,
// key or URL: sameBackendIdentity()), i.e. the known active route no longer applies; a new WiFi,
// APN, plate or phone alone is false.
bool credentialsApply(const DeviceCredentials& fresh);

// The critical-alert SMS (grip-alarm contracts C5): is a phone set, and the phone plus what the
// SMS calls the truck (its plate, else its truck id). credentialsSmsTarget() is false with no
// phone.
bool credentialsHasAlertPhone();
bool credentialsSmsTarget(String& phone, String& vehicle);

// The Pi answered NOCONFIG: nothing is provisioned there. Clears the stale flag so we don't ask
// every poll; keeps any NVS copy (still the best guess) until the Pi has one again.
void credentialsNoneOnPi();
