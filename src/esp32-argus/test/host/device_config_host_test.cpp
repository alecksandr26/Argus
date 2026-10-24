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

static void networkSettingsAreOptionalAndVerbatim() {
  DeviceCredentials c;
  CHECK(parseConfigLine(GOOD, c) == ConfigLineResult::Ok);
  CHECK(c.wifiSsid.empty() && c.gprsApn.empty());
  CHECK(!c.useGprs());  // no APN: WiFi only

  const char* line =
      R"(CONFIG {"truck_id":"t","device_api_key":"k","backend_url":"https://x",)"
      R"("wifi_ssid":"Casa ","wifi_password":"pw","gprs_apn":"internet.itelcel.com",)"
      R"("gprs_user":"webgprs","gprs_password":"webgprs2002"})";
  CHECK(parseConfigLine(line, c) == ConfigLineResult::Ok);
  CHECK(c.wifiSsid == "Casa ");  // trailing space kept
  CHECK(c.wifiPassword == "pw");
  CHECK(c.gprsApn == "internet.itelcel.com");
  CHECK(c.gprsUser == "webgprs");
  CHECK(c.gprsPassword == "webgprs2002");
  CHECK(c.useGprs());
}

static void malformedNetworkSettingRejectsTheLine() {
  std::string overlong = R"(CONFIG {"truck_id":"t","device_api_key":"k","backend_url":"https://x",)";
  overlong += R"("wifi_ssid":")" + std::string(201, 's') + R"("})";
  const char* wrongType =
      R"(CONFIG {"truck_id":"t","device_api_key":"k","backend_url":"https://x","gprs_apn":5})";
  DeviceCredentials c;
  c.apiKey = "previous";
  CHECK(parseConfigLine(overlong, c) == ConfigLineResult::Malformed);
  CHECK(parseConfigLine(wrongType, c) == ConfigLineResult::Malformed);
  CHECK(c.apiKey == "previous");
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

// A routine CONFIG refresh with the same truck, key and URL keeps the known active route; a new
// WiFi or APN alone does too; a new truck, key or URL doesn't (refreshCredentialsIfStale).
static void backendIdentity() {
  DeviceCredentials a;
  CHECK(parseConfigLine(GOOD, a) == ConfigLineResult::Ok);
  DeviceCredentials b = a;
  CHECK(sameBackendIdentity(a, b));
  b.wifiSsid = "hotspot";
  b.gprsApn = "internet.itelcel.com";
  CHECK(sameBackendIdentity(a, b));
  DeviceCredentials key = a;
  key.apiKey = "rotated";
  CHECK(!sameBackendIdentity(a, key));
  DeviceCredentials truck = a;
  truck.truckId = "64b0a0000000000000000002";
  CHECK(!sameBackendIdentity(a, truck));
  DeviceCredentials url = a;
  url.backendUrl = "http://192.168.1.20:8000";
  CHECK(!sameBackendIdentity(a, url));
  CHECK(!sameBackendIdentity(DeviceCredentials(), a));  // the first CONFIG after an empty NVS
}

// --- The SMS fields (grip-alarm contracts C1.4) ---

static const char* withSms(const char* plate, const char* phone) {
  static std::string line;
  line = R"(CONFIG {"truck_id":"t","device_api_key":"k","backend_url":"https://x")";
  if (plate) line += std::string(R"(,"truck_plate":)") + plate;
  if (phone) line += std::string(R"(,"alert_phone":)") + phone;
  line += "}";
  return line.c_str();
}

static void plateAndPhoneAreReadWhenGiven() {
  DeviceCredentials c;
  CHECK(parseConfigLine(withSms(R"("JAL-123-A")", R"("+523312345678")"), c) ==
        ConfigLineResult::Ok);
  CHECK(c.truckPlate == "JAL-123-A");
  CHECK(c.alertPhone == "+523312345678");
}

static void plateAndPhoneAreOptional() {
  DeviceCredentials c;
  CHECK(parseConfigLine(GOOD, c) == ConfigLineResult::Ok);
  CHECK(c.truckPlate.empty() && c.alertPhone.empty());
  CHECK(parseConfigLine(withSms("null", "null"), c) == ConfigLineResult::Ok);
  CHECK(c.truckPlate.empty() && c.alertPhone.empty());
}

// A bad plate or phone is ignored, never Malformed: the credentials still apply.
static void aBadPlateOrPhoneIsIgnoredNotMalformed() {
  struct Case {
    const char* plate;
    const char* phone;
  } cases[] = {
      {R"("ABCDEFGHIJKLMNOPQ")", R"("3312345678")"},  // 17 chars; no "+"
      {"42", "523312345678"},                         // not strings
      {R"("")", R"("+023312345678")"},                // empty plate; leading 0
      {"[]", R"("+52 33 1234 5678")"},                 // spaces
      {"{}", R"("+1234567")"},                         // 7 digits: too short
      {R"("ok")", R"("+1234567890123456")"},          // 16 digits: too long
      {R"("ok")", R"("+52331234567x")"},
  };
  for (const Case& k : cases) {
    DeviceCredentials c;
    c.alertPhone = "+520000000000";  // from an earlier CONFIG: replaced, not kept
    CHECK(parseConfigLine(withSms(k.plate, k.phone), c) == ConfigLineResult::Ok);
    CHECK(c.alertPhone.empty());
    CHECK(c.apiKey == "k");  // the credentials applied
  }
  DeviceCredentials c;
  CHECK(parseConfigLine(withSms(R"("ABCDEFGHIJKLMNOPQ")", nullptr), c) == ConfigLineResult::Ok);
  CHECK(c.truckPlate.empty());
  CHECK(parseConfigLine(withSms(R"("ABCDEFGHIJKLMNOP")", nullptr), c) == ConfigLineResult::Ok);
  CHECK(c.truckPlate == "ABCDEFGHIJKLMNOP");  // exactly 16 is fine
}

static void e164() {
  CHECK(isE164Phone("+523312345678"));
  CHECK(isE164Phone("+12345678"));          // 8 digits, the shortest
  CHECK(isE164Phone("+123456789012345"));   // 15 digits, the longest
  CHECK(!isE164Phone("+1234567"));
  CHECK(!isE164Phone("+1234567890123456"));
  CHECK(!isE164Phone("+0123456789"));
  CHECK(!isE164Phone("523312345678"));
  CHECK(!isE164Phone("+"));
  CHECK(!isE164Phone(""));
  CHECK(!isE164Phone(nullptr));
  CHECK(!isE164Phone("+52-3312345678"));
}

static void phonesAreMaskedForTheLog() {
  CHECK(maskPhone("+523312345678") == "+52******5678");
  CHECK(maskPhone("+12345678") == "+12**5678");
  CHECK(maskPhone("") == "****");
}

static void thePlateAndPhoneAreNotPartOfTheBackendIdentity() {
  DeviceCredentials a;
  CHECK(parseConfigLine(GOOD, a) == ConfigLineResult::Ok);
  DeviceCredentials b = a;
  b.truckPlate = "JAL-123-A";
  b.alertPhone = "+523312345678";
  CHECK(sameBackendIdentity(a, b));
}

int main() {
  RUN(parsesTheThreeFields);
  RUN(plainHttpBenchUrl);
  RUN(noConfig);
  RUN(malformedLeavesOutputUntouched);
  RUN(overlongValueIsRejected);
  RUN(networkSettingsAreOptionalAndVerbatim);
  RUN(malformedNetworkSettingRejectsTheLine);
  RUN(validity);
  RUN(backendIdentity);
  RUN(plateAndPhoneAreReadWhenGiven);
  RUN(plateAndPhoneAreOptional);
  RUN(aBadPlateOrPhoneIsIgnoredNotMalformed);
  RUN(e164);
  RUN(phonesAreMaskedForTheLog);
  RUN(thePlateAndPhoneAreNotPartOfTheBackendIdentity);
  return report("device_config_host_test");
}
