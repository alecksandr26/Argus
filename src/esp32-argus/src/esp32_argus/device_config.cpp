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
  out = parsed;
  return ConfigLineResult::Ok;
}
