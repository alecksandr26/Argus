// Parsing the Pi's CONFIG reply (cv-argus sender/protocol.py format_config): the backend
// credentials this device fetches over Bluetooth instead of having them flashed into config.h.
// SOURCES: device_config.cpp
// NEEDS: arduinojson
#include <string>

#include "check.h"
#include "device_config.h"

static const char* GOOD =
    R"(CONFIG {"truck_id":"64b0a0000000000000000001","device_api_key":"k3y-AbC_123",)"
    R"("backend_url":"https://d123.cloudfront.net/"})";

static void parsesTheThreeFields() {
  DeviceCredentials c;
  CHECK(parseConfigLine(GOOD, c) == ConfigLineResult::Ok);
  CHECK(c.truckId == "64b0a0000000000000000001");
  CHECK(c.apiKey == "k3y-AbC_123");
  CHECK(c.backendUrl == "https://d123.cloudfront.net");  // trailing slash dropped
  CHECK(c.valid());
}

static void plainHttpBenchUrl() {
  DeviceCredentials c;
  const char* line =
      R"(CONFIG {"truck_id":"t","device_api_key":"k","backend_url":"http://192.168.1.20:8000"})";
  CHECK(parseConfigLine(line, c) == ConfigLineResult::Ok);
  CHECK(c.backendUrl == "http://192.168.1.20:8000");
}

static void noConfig() {
  DeviceCredentials c;
  c.truckId = "kept";
  CHECK(parseConfigLine("NOCONFIG", c) == ConfigLineResult::NoConfig);
  CHECK(c.truckId == "kept");  // untouched
}

static void malformedLeavesOutputUntouched() {
  const char* bad[] = {
      "",
      "CONFIG",
      "CONFIG {not json",
      "CONFIG []",
      "PULL 5",
      R"(CONFIG {"truck_id":"t","device_api_key":"k"})",                                // no URL
      R"(CONFIG {"truck_id":"","device_api_key":"k","backend_url":"https://x"})",       // empty id
      R"(CONFIG {"truck_id":"t","device_api_key":"k","backend_url":"ftp://x"})",        // scheme
      R"(CONFIG {"truck_id":"t","device_api_key":"k","backend_url":"///"})",            // empty
      R"(CONFIG {"truck_id":1,"device_api_key":"k","backend_url":"https://x"})",        // type
  };
  for (const char* line : bad) {
    DeviceCredentials c;
    c.apiKey = "previous";
    CHECK(parseConfigLine(line, c) == ConfigLineResult::Malformed);
    CHECK(c.apiKey == "previous");
  }
}

static void overlongValueIsRejected() {
  std::string line = R"(CONFIG {"truck_id":"t","device_api_key":")";
  line += std::string(201, 'k');
  line += R"(","backend_url":"https://x"})";
  DeviceCredentials c;
  CHECK(parseConfigLine(line, c) == ConfigLineResult::Malformed);
}

static void validity() {
  DeviceCredentials c;
  CHECK(!c.valid());
  c.truckId = "t";
  c.apiKey = "k";
  c.backendUrl = "https://x";
  CHECK(c.valid());
  c.backendUrl = "x";
  CHECK(!c.valid());
}

int main() {
  RUN(parsesTheThreeFields);
  RUN(plainHttpBenchUrl);
  RUN(noConfig);
  RUN(malformedLeavesOutputUntouched);
  RUN(overlongValueIsRejected);
  RUN(validity);
  return report("device_config_host_test");
}
