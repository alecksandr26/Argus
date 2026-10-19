// fusion.cpp against the shared fusion vectors (X-15): the same scripted readings and the
// decisions cv-argus's fusion_contract.py (the reference) makes for them, generated into
// test/vectors/fusion_vectors.json by generate_fusion_vectors.py. The Python side replays the
// same file in src/tests/edge_chain/test_firmware_vectors.py, so a change to either copy that
// the other doesn't get fails one of the two.
// SOURCES: fusion.cpp
// NEEDS: arduinojson
#include <ArduinoJson.h>

#include <fstream>
#include <sstream>
#include <string>

#include "check.h"
#include "fusion.h"

static std::string vectorsPath() {
  std::string here = __FILE__;  // run.sh compiles this file by its absolute path
  return here.substr(0, here.find_last_of('/')) + "/../vectors/fusion_vectors.json";
}

static std::string readFile(const std::string& path) {
  std::ifstream in(path);
  std::stringstream ss;
  ss << in.rdbuf();
  return ss.str();
}

static int casesRun = 0;

static void runCase(JsonObject testCase, const FusionConfig& config) {
  std::string name = testCase["name"].as<std::string>();
  FusionOrchestrator f(config);
  int step = 0;
  for (JsonObject s : testCase["steps"].as<JsonArray>()) {
    step++;
    if (std::string(s["op"].as<const char*>()) == "posted") {
      f.recordAlertPosted(s["id"].as<std::string>());
      continue;
    }
    FusionReading reading{s["drowsy"].as<bool>() ? DrowsyState::DROWSY : DrowsyState::NOT_DROWSY,
                          std::string(s["grip"].as<const char*>()) == "good" ? GripState::GOOD
                                                                              : GripState::BAD,
                          s["at"].as<float>()};
    FusionDecision d = f.update(reading);
    JsonObject expect = s["expect"];

    bool severityOk = std::string(severityToString(d.severity)) == expect["severity"].as<std::string>();
    bool alertOk;
    if (expect["alert"].isNull()) {
      alertOk = !d.alert.present;
    } else {
      JsonObject a = expect["alert"];
      std::string related = a["related"].isNull() ? "" : a["related"].as<std::string>();
      alertOk = d.alert.present &&
                std::string(severityToString(d.alert.severityLevel)) == a["severity"].as<std::string>() &&
                (d.alert.gripStatus == GripState::GOOD ? "good" : "bad") == a["grip"].as<std::string>() &&
                (d.alert.isEscalation ? d.alert.relatedAlertId : std::string()) == related;
    }
    std::string resolved = expect["resolved"].isNull() ? "" : expect["resolved"].as<std::string>();
    bool resolvedOk = (d.hasResolvedIncident ? d.resolvedIncidentId : std::string()) == resolved;

    if (!(severityOk && alertOk && resolvedOk)) {
      std::printf("FAIL %s step %d (at=%.1f): severity %s alert %s resolved %s\n", name.c_str(), step,
                  reading.at, severityOk ? "ok" : "differs", alertOk ? "ok" : "differs",
                  resolvedOk ? "ok" : "differs");
      failures++;
    }
  }
  casesRun++;
}

int main() {
  std::string text = readFile(vectorsPath());
  JsonDocument doc;
  CHECK(!text.empty());
  CHECK(deserializeJson(doc, text) == DeserializationError::Ok);

  FusionConfig config;
  config.debounceSeconds = doc["config"]["debounce_seconds"].as<float>();
  config.escalationSeconds = doc["config"]["escalation_seconds"].as<float>();
  config.recoverySeconds = doc["config"]["recovery_seconds"].as<float>();

  for (JsonObject testCase : doc["cases"].as<JsonArray>()) runCase(testCase, config);
  CHECK(casesRun >= 10);  // the file really was read
  return report("fusion_vectors_host_test");
}
