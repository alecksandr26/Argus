#pragma once

#include <Arduino.h>

// This truck's backend credentials, as the Pi serves them over Bluetooth -- cv-argus's
// `CONFIG` reply (src/cv-argus/src/sender/protocol.py, sender/device_config.py):
//
//   CONFIG {"truck_id":"...","device_api_key":"...","backend_url":"https://...",
//           "wifi_ssid":"...","wifi_password":"...","gprs_apn":"...","gprs_user":"...",
//           "gprs_password":"..."}      (the five network fields are optional)
//   NOCONFIG                     (the Pi has no argus-device.env installed)
//
// The dashboard issues them (Fleet -> truck -> "Generate device credentials"); the Pi holds
// them; this device only caches them (credentials.*). Pure parsing only -- no Bluetooth, no
// NVS -- so test/host/device_config_host_test.cpp can run it on a dev machine.

struct DeviceCredentials {
  String truckId;
  String apiKey;
  String backendUrl;  // no trailing slash; http:// (LAN bench) or https:// (AWS)

  // Optional network settings, also from the Pi (its .env): "" = not provided. Kept verbatim
  // (an SSID may end in a space). No APN = no mobile data, WiFi only.
  String wifiSsid;
  String wifiPassword;
  String gprsApn;
  String gprsUser;
  String gprsPassword;

  bool valid() const;
  bool useGprs() const { return gprsApn.length() > 0; }
};

// Same truck, key and backend URL (the network settings aside): what the backend sees of this
// device. A change means the known active route may no longer apply (credentials.h).
bool sameBackendIdentity(const DeviceCredentials& a, const DeviceCredentials& b);

enum class ConfigLineResult : uint8_t { Ok, NoConfig, Malformed };

// Parses one line of the Pi's CONFIG reply into `out` (only written on Ok). Malformed covers
// bad JSON, a missing/empty field, a non-http(s) URL, and an over-long value.
ConfigLineResult parseConfigLine(const String& line, DeviceCredentials& out);
