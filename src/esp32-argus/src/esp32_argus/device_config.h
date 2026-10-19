#pragma once

#include <Arduino.h>

// This truck's backend credentials, as the Pi serves them over Bluetooth -- cv-argus's
// `CONFIG` reply (src/cv-argus/src/sender/protocol.py, sender/device_config.py):
//
//   CONFIG {"truck_id":"...","device_api_key":"...","backend_url":"https://..."}
//   NOCONFIG                     (the Pi has no argus-device.env installed)
//
// The dashboard issues them (Fleet -> truck -> "Generate device credentials"); the Pi holds
// them; this device only caches them (credentials.*). Pure parsing only -- no Bluetooth, no
// NVS -- so test/host/device_config_host_test.cpp can run it on a dev machine.

struct DeviceCredentials {
  String truckId;
  String apiKey;
  String backendUrl;  // no trailing slash; http:// (LAN bench) or https:// (AWS)

  bool valid() const;
};

enum class ConfigLineResult : uint8_t { Ok, NoConfig, Malformed };

// Parses one line of the Pi's CONFIG reply into `out` (only written on Ok). Malformed covers
// bad JSON, a missing/empty field, a non-http(s) URL, and an over-long value.
ConfigLineResult parseConfigLine(const String& line, DeviceCredentials& out);
