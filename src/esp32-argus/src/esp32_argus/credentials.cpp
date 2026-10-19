#include "credentials.h"

#include <Preferences.h>

#include "config.h"

namespace {
Preferences prefs;
DeviceCredentials current;
bool stale = true;
unsigned long noConfigAt = 0;
bool sawNoConfig = false;
unsigned long lastFetchedAt = 0;
bool fetchedOnce = false;

const char NVS_NAMESPACE[] = "argus";

bool sameAs(const DeviceCredentials& a, const DeviceCredentials& b) {
  return a.truckId == b.truckId && a.apiKey == b.apiKey && a.backendUrl == b.backendUrl &&
         a.wifiSsid == b.wifiSsid && a.wifiPassword == b.wifiPassword &&
         a.gprsApn == b.gprsApn && a.gprsUser == b.gprsUser && a.gprsPassword == b.gprsPassword;
}

void logLoaded(const char* from) {
  // Never print the key itself -- only which truck and where it reports to.
  Serial.print("[creds] Using credentials for truck ");
  Serial.print(current.truckId);
  Serial.print(" -> ");
  Serial.print(current.backendUrl);
  Serial.print(" (");
  Serial.print(from);
  Serial.println(")");
  // The network names are fine to print; their passwords never are.
  Serial.print("[creds] WiFi: ");
  Serial.print(current.wifiSsid.length() > 0 ? "\"" + current.wifiSsid + "\"" : String("none"));
  Serial.print(", mobile data APN: ");
  Serial.println(current.useGprs() ? current.gprsApn : String("none (WiFi only)"));
}
}  // namespace

void credentialsBegin() {
  prefs.begin(NVS_NAMESPACE, /*readOnly=*/false);
  DeviceCredentials stored;
  stored.truckId = prefs.getString("truck_id", "");
  stored.apiKey = prefs.getString("api_key", "");
  stored.backendUrl = prefs.getString("backend_url", "");
  stored.wifiSsid = prefs.getString("wifi_ssid", "");
  stored.wifiPassword = prefs.getString("wifi_pass", "");
  stored.gprsApn = prefs.getString("gprs_apn", "");
  stored.gprsUser = prefs.getString("gprs_user", "");
  stored.gprsPassword = prefs.getString("gprs_pass", "");
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
  // Even when everything works, re-ask now and then so .env changes on the Pi arrive.
  if (fetchedOnce && millis() - lastFetchedAt >= CONFIG_REFRESH_INTERVAL_MS) {
    fetchedOnce = false;
    stale = true;
  }
  return stale;
}

void credentialsMarkStale() { stale = true; }

void credentialsApply(const DeviceCredentials& fresh) {
  stale = false;
  sawNoConfig = false;
  fetchedOnce = true;
  lastFetchedAt = millis();
  if (sameAs(fresh, current)) return;
  current = fresh;
  prefs.putString("truck_id", current.truckId);
  prefs.putString("api_key", current.apiKey);
  prefs.putString("backend_url", current.backendUrl);
  prefs.putString("wifi_ssid", current.wifiSsid);
  prefs.putString("wifi_pass", current.wifiPassword);
  prefs.putString("gprs_apn", current.gprsApn);
  prefs.putString("gprs_user", current.gprsUser);
  prefs.putString("gprs_pass", current.gprsPassword);
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
