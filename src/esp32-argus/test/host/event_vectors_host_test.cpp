// pi_event_policy.cpp against the shared C1.5 event vectors (test/vectors/event_vectors.json):
// the contract's table of which Pi records are backend events. The edge_chain Python mirror can
// replay the same file, so the two copies of the rule can't drift apart silently.
// SOURCES: pi_event_policy.cpp
// NEEDS: arduinojson
#include <ArduinoJson.h>

#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>

#include "check.h"
#include "pi_event_policy.h"

static std::string vectorsPath() {
  std::string here = __FILE__;  // run.sh compiles this file by its absolute path
  return here.substr(0, here.find_last_of('/')) + "/../vectors/event_vectors.json";
}

static std::string readFile(const std::string& path) {
  std::ifstream in(path);
  std::stringstream ss;
  ss << in.rdbuf();
  return ss.str();
}

static const char* expectName(PiEventKind k) {
  switch (k) {
    case PiEventKind::None: return "none";
    case PiEventKind::AlertMedium: return "alert_medium";
    case PiEventKind::AlertCritical: return "alert_critical";
    case PiEventKind::DriverOk: return "ok";
  }
  return "?";
}

int main() {
  JsonDocument doc;
  CHECK(deserializeJson(doc, readFile(vectorsPath())) == DeserializationError::Ok);
  int cases = 0;
  for (JsonObject c : doc["cases"].as<JsonArray>()) {
    PiRecord r;
    r.id = "v";
    r.transition = c["transition"].as<bool>();
    r.severity = c["severity"] | "";
    r.previousSeverity = c["previous_severity"].isNull() ? "" : c["previous_severity"].as<std::string>();
    std::string got = expectName(classifyPiEvent(r));
    std::string want = c["expect"].as<std::string>();
    if (got != want) {
      std::printf("FAIL case %s: got %s, want %s\n", c["name"].as<const char*>(), got.c_str(),
                  want.c_str());
      failures++;
    }
    cases++;
  }
  CHECK(cases >= 20);
  return report("event_vectors_host_test");
}
