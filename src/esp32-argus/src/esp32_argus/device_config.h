#pragma once

#include <Arduino.h>

// This truck's backend credentials, as the Pi serves them over Bluetooth -- cv-argus's
// `CONFIG` reply (src/cv-argus/src/sender/protocol.py, sender/device_config.py):
//
//   CONFIG {"truck_id":"...","device_api_key":"...","backend_url":"https://...",
//           "wifi_ssid":"...","wifi_password":"...","gprs_apn":"...","gprs_user":"...",
//           "gprs_password":"...","truck_plate":"...","alert_phone":"+52..."}
//                                (everything after backend_url is optional)
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

  // For the critical-alert SMS (docs/designs/grip-alarm-contracts.md C1.4, C5), "" = not provided.
  // RAM only: credentials.cpp never writes them to NVS (the phone is personal data), so after a
  // reboot they are empty until the next CONFIG. A plate longer than 16 chars, or a phone that
  // isn't E.164, is ignored (left "") -- never a reason to reject the line.
  String truckPlate;
  String alertPhone;

  bool valid() const;
  bool useGprs() const { return gprsApn.length() > 0; }
};

// Same truck, key and backend URL (the network settings, plate and phone aside): what the backend
// sees of this device. A change means the known active route may no longer apply (credentials.h).
bool sameBackendIdentity(const DeviceCredentials& a, const DeviceCredentials& b);

enum class ConfigLineResult : uint8_t { Ok, NoConfig, Malformed };

// Parses one line of the Pi's CONFIG reply into `out` (only written on Ok). Malformed covers
// bad JSON, a missing/empty field, a non-http(s) URL, and an over-long value -- never the plate
// or the phone, which are ignored instead.
ConfigLineResult parseConfigLine(const String& line, DeviceCredentials& out);

// The truck_plate limit (C1.4): a longer plate is ignored.
constexpr size_t kTruckPlateMaxLength = 16;

// E.164, the phone format everywhere (C1): ^\+[1-9][0-9]{7,14}$, e.g. "+523312345678".
bool isE164Phone(const char* phone);

// A phone for the log: "+52******5678" (the first 3 and last 4 chars kept). Never log it in full.
String maskPhone(const String& phone);
