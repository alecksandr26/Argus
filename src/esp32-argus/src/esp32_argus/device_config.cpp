#include "device_config.h"

#include <ArduinoJson.h>
#include <string.h>

namespace {

// Longer than any real value (a 24-char ObjectId, a 43-char token_urlsafe(32) key, a short
// URL); anything past this is a broken reply, not something to store in NVS.
constexpr size_t MAX_FIELD_LEN = 200;

const char CONFIG_PREFIX[] = "CONFIG ";
const char NOCONFIG_LINE[] = "NOCONFIG";

bool startsWith(const char* s, const char* prefix) {
  return strncmp(s, prefix, strlen(prefix)) == 0;
}

// Copies a JSON string field into `out`, trimming trailing '/' when `trimSlash`. Only uses what
// Arduino's String and std::string (the host shim) share: construction from a const char*.
bool readField(JsonVariantConst value, String& out, bool trimSlash) {
  const char* s = value.as<const char*>();
  if (s == nullptr) return false;
  size_t n = strlen(s);
  if (trimSlash) {
    while (n > 0 && s[n - 1] == '/') n--;
  }
  if (n == 0 || n > MAX_FIELD_LEN) return false;
  char buf[MAX_FIELD_LEN + 1];
  memcpy(buf, s, n);
  buf[n] = '\0';
  out = String(buf);
  return true;
}

// An optional field: absent (or null) leaves `out` empty and is fine; present must be a string
// within MAX_FIELD_LEN. Copied verbatim -- no trimming, an SSID may end in a space.
bool readOptionalField(JsonVariantConst value, String& out) {
  if (value.isNull()) {
    out = String("");
    return true;
  }
  const char* s = value.as<const char*>();
  if (s == nullptr || strlen(s) > MAX_FIELD_LEN) return false;
  out = String(s);
  return true;
}

// The SMS fields (C1.4): anything unusable -- absent, not a string, too long, not E.164 -- reads as
// "" and never rejects the line, so a typo in the plate or the phone can't take the truck's
// backend credentials offline.
String readPlate(JsonVariantConst value) {
  const char* s = value.as<const char*>();
  if (s == nullptr || strlen(s) > kTruckPlateMaxLength) return String("");
  return String(s);
}

String readPhone(JsonVariantConst value) {
  const char* s = value.as<const char*>();
  if (s == nullptr || !isE164Phone(s)) return String("");
  return String(s);
}

}  // namespace

bool DeviceCredentials::valid() const {
  const char* url = backendUrl.c_str();
  return truckId.length() > 0 && apiKey.length() > 0 &&
         (startsWith(url, "http://") || startsWith(url, "https://"));
}

ConfigLineResult parseConfigLine(const String& line, DeviceCredentials& out) {
  const char* s = line.c_str();
  if (strcmp(s, NOCONFIG_LINE) == 0) return ConfigLineResult::NoConfig;
  if (!startsWith(s, CONFIG_PREFIX)) return ConfigLineResult::Malformed;

  JsonDocument doc;
  if (deserializeJson(doc, s + strlen(CONFIG_PREFIX)) != DeserializationError::Ok) {
    return ConfigLineResult::Malformed;
  }
  DeviceCredentials parsed;
  if (!readField(doc["truck_id"], parsed.truckId, false) ||
      !readField(doc["device_api_key"], parsed.apiKey, false) ||
      !readField(doc["backend_url"], parsed.backendUrl, true) || !parsed.valid()) {
    return ConfigLineResult::Malformed;
  }
  if (!readOptionalField(doc["wifi_ssid"], parsed.wifiSsid) ||
      !readOptionalField(doc["wifi_password"], parsed.wifiPassword) ||
      !readOptionalField(doc["gprs_apn"], parsed.gprsApn) ||
      !readOptionalField(doc["gprs_user"], parsed.gprsUser) ||
      !readOptionalField(doc["gprs_password"], parsed.gprsPassword)) {
    return ConfigLineResult::Malformed;
  }
  parsed.truckPlate = readPlate(doc["truck_plate"]);
  parsed.alertPhone = readPhone(doc["alert_phone"]);
  out = parsed;
  return ConfigLineResult::Ok;
}

bool sameBackendIdentity(const DeviceCredentials& a, const DeviceCredentials& b) {
  return a.truckId == b.truckId && a.apiKey == b.apiKey && a.backendUrl == b.backendUrl;
}

bool isE164Phone(const char* phone) {
  if (phone == nullptr || phone[0] != '+' || phone[1] < '1' || phone[1] > '9') return false;
  size_t digits = 0;
  for (const char* p = phone + 1; *p; p++) {
    if (*p < '0' || *p > '9') return false;
    digits++;
  }
  return digits >= 8 && digits <= 15;  // the first digit + 7..14 more
}

String maskPhone(const String& phone) {
  const char* s = phone.c_str();
  size_t n = strlen(s);
  if (n < 8 || n > 16) return String("****");  // not an E.164 phone
  char buf[17];
  for (size_t i = 0; i < n; i++) buf[i] = i < 3 || i >= n - 4 ? s[i] : '*';
  buf[n] = '\0';
  return String(buf);
}
