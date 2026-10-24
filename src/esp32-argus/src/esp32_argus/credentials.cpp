#include "credentials.h"

#include <Preferences.h>

#include "config.h"
#include "rtos_lock.h"

namespace {
Preferences prefs;  // the loop task only (credentialsBegin / credentialsApply)
SemaphoreHandle_t mutex = nullptr;

// Guarded by `mutex`.
DeviceCredentials current;
uint32_t generation = 0;
bool stale = true;
unsigned long noConfigAt = 0;
bool sawNoConfig = false;
unsigned long lastFetchedAt = 0;
bool fetchedOnce = false;

const char NVS_NAMESPACE[] = "argus";

// The fields cached in NVS. The plate and the SMS phone are not among them: RAM only (C1.4).
bool sameStored(const DeviceCredentials& a, const DeviceCredentials& b) {
  return a.truckId == b.truckId && a.apiKey == b.apiKey && a.backendUrl == b.backendUrl &&
         a.wifiSsid == b.wifiSsid && a.wifiPassword == b.wifiPassword &&
         a.gprsApn == b.gprsApn && a.gprsUser == b.gprsUser && a.gprsPassword == b.gprsPassword;
}

bool sameSmsTarget(const DeviceCredentials& a, const DeviceCredentials& b) {
  return a.truckPlate == b.truckPlate && a.alertPhone == b.alertPhone;
}

void logSmsTarget(const DeviceCredentials& c) {
  // The phone is personal data: masked, never in full (C1.3).
  Serial.print("[creds] SMS alert phone: ");
  Serial.print(c.alertPhone.length() > 0 ? maskPhone(c.alertPhone)
                                         : String("none (no SMS on a critical)"));
  Serial.print(", plate: ");
  Serial.println(c.truckPlate.length() > 0 ? c.truckPlate : String("none (the SMS names the truck id)"));
}

void logLoaded(const DeviceCredentials& c, const char* from) {
  // Never print the key itself -- only which truck and where it reports to.
  Serial.print("[creds] Using credentials for truck ");
  Serial.print(c.truckId);
  Serial.print(" -> ");
  Serial.print(c.backendUrl);
  Serial.print(" (");
  Serial.print(from);
  Serial.println(")");
  // The network names are fine to print; their passwords never are.
  Serial.print("[creds] WiFi: ");
  Serial.print(c.wifiSsid.length() > 0 ? "\"" + c.wifiSsid + "\"" : String("none"));
  Serial.print(", mobile data APN: ");
  Serial.println(c.useGprs() ? c.gprsApn : String("none (WiFi only)"));
}
}  // namespace

void credentialsBegin() {
  if (!mutex) mutex = xSemaphoreCreateMutex();
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
  bool usable = stored.valid();
  {
    RtosLock lock(mutex);
    if (usable) {
      current = stored;
      generation++;
    }
    stale = true;
  }
  if (usable) {
    logLoaded(stored, "NVS cache");
  } else {
    Serial.println("[creds] No cached credentials -- waiting for the Pi's CONFIG");
  }
}

bool credentialsReady() {
  RtosLock lock(mutex);
  return current.valid();
}

uint32_t credentialsGeneration() {
  RtosLock lock(mutex);
  return generation;
}

DeviceCredentials credentialsCopy() {
  RtosLock lock(mutex);
  return current;
}

bool credentialsCopyIfChanged(DeviceCredentials& out, uint32_t& seen) {
  RtosLock lock(mutex);
  if (seen == generation) return false;
  out = current;
  seen = generation;
  return true;
}

bool credentialsStale() {
  RtosLock lock(mutex);
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

void credentialsMarkStale() {
  RtosLock lock(mutex);
  stale = true;
}

bool credentialsApply(const DeviceCredentials& fresh) {
  bool identityChanged, storedChanged, smsChanged;
  {
    RtosLock lock(mutex);
    stale = false;
    sawNoConfig = false;
    fetchedOnce = true;
    lastFetchedAt = millis();
    storedChanged = !sameStored(fresh, current);
    smsChanged = !sameSmsTarget(fresh, current);
    if (!storedChanged && !smsChanged) return false;
    identityChanged = !sameBackendIdentity(fresh, current);
    current = fresh;
    generation++;
  }
  if (smsChanged) logSmsTarget(fresh);
  if (!storedChanged) return false;  // only the plate/phone: RAM only, no NVS write
  // NVS writes outside the lock: the sender task never waits on flash for a copy.
  prefs.putString("truck_id", fresh.truckId);
  prefs.putString("api_key", fresh.apiKey);
  prefs.putString("backend_url", fresh.backendUrl);
  prefs.putString("wifi_ssid", fresh.wifiSsid);
  prefs.putString("wifi_pass", fresh.wifiPassword);
  prefs.putString("gprs_apn", fresh.gprsApn);
  prefs.putString("gprs_user", fresh.gprsUser);
  prefs.putString("gprs_pass", fresh.gprsPassword);
  logLoaded(fresh, "fresh from the Pi, saved to NVS");
  return identityChanged;
}

bool credentialsHasAlertPhone() {
  RtosLock lock(mutex);
  return current.alertPhone.length() > 0;
}

bool credentialsSmsTarget(String& phone, String& vehicle) {
  RtosLock lock(mutex);
  phone = current.alertPhone;
  vehicle = current.truckPlate.length() > 0 ? current.truckPlate : current.truckId;
  return phone.length() > 0;
}

void credentialsNoneOnPi() {
  bool ready;
  {
    RtosLock lock(mutex);
    stale = false;
    sawNoConfig = true;
    noConfigAt = millis();
    ready = current.valid();
  }
  Serial.println(ready ? "[creds] Pi has no argus-device.env -- keeping the NVS copy"
                       : "[creds] Pi has no argus-device.env -- can't reach the backend yet");
}
