#include "credentials.h"

#include <Preferences.h>

#include "config.h"

namespace {
Preferences prefs;
DeviceCredentials current;
bool stale = true;
unsigned long noConfigAt = 0;
bool sawNoConfig = false;

const char NVS_NAMESPACE[] = "argus";

void logLoaded(const char* from) {
  // Never print the key itself -- only which truck and where it reports to.
  Serial.print("[creds] Using credentials for truck ");
  Serial.print(current.truckId);
  Serial.print(" -> ");
  Serial.print(current.backendUrl);
  Serial.print(" (");
  Serial.print(from);
  Serial.println(")");
}
}  // namespace

void credentialsBegin() {
  prefs.begin(NVS_NAMESPACE, /*readOnly=*/false);
  DeviceCredentials stored;
  stored.truckId = prefs.getString("truck_id", "");
  stored.apiKey = prefs.getString("api_key", "");
  stored.backendUrl = prefs.getString("backend_url", "");
  if (stored.valid()) {
    current = stored;
    logLoaded("NVS cache");
  } else {
    Serial.println("[creds] No cached credentials -- waiting for the Pi's CONFIG");
  }
  stale = true;
}

bool credentialsReady() { return current.valid(); }

const DeviceCredentials& credentials() { return current; }

bool credentialsStale() {
  // After a NOCONFIG, ask again only every CONFIG_RETRY_INTERVAL_MS, not on every poll.
  if (sawNoConfig && millis() - noConfigAt >= CONFIG_RETRY_INTERVAL_MS) {
    sawNoConfig = false;
    stale = true;
  }
  return stale;
}

void credentialsMarkStale() { stale = true; }

void credentialsApply(const DeviceCredentials& fresh) {
  stale = false;
  sawNoConfig = false;
  if (fresh.truckId == current.truckId && fresh.apiKey == current.apiKey &&
      fresh.backendUrl == current.backendUrl) {
    return;
  }
  current = fresh;
  prefs.putString("truck_id", current.truckId);
  prefs.putString("api_key", current.apiKey);
  prefs.putString("backend_url", current.backendUrl);
  logLoaded("fresh from the Pi, saved to NVS");
}

void credentialsNoneOnPi() {
  stale = false;
  sawNoConfig = true;
  noConfigAt = millis();
  Serial.println(credentialsReady()
                     ? "[creds] Pi has no argus-device.env -- keeping the NVS copy"
                     : "[creds] Pi has no argus-device.env -- can't reach the backend yet");
}
