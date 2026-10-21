#include "pi_record.h"

#include <ArduinoJson.h>  // requires ArduinoJson v7+

namespace {

// The record's top-level "id", read straight from the raw line. Used when ArduinoJson rejects
// the line (cv-argus writes a NaN probability as a bare NaN token, which isn't JSON): the record
// can still be ACKed away instead of blocking the Pi's queue. Only a complete object counts, and
// only an "id" at the top level, never one inside the payload.
std::string topLevelId(const std::string& line) {
  int depth = 0;
  bool inString = false;
  bool escaped = false;
  std::string token;
  std::string lastKey;
  std::string id;
  for (size_t i = 0; i < line.size(); i++) {
    char c = line[i];
    if (inString) {
      if (escaped) {
        token += c;
        escaped = false;
      } else if (c == '\\') {
        escaped = true;
      } else if (c == '"') {
        inString = false;
        if (depth == 1) {
          size_t j = i + 1;
          while (j < line.size() && line[j] == ' ') j++;
          if (j < line.size() && line[j] == ':') {
            lastKey = token;  // a key
          } else if (lastKey == "id") {
            id = token;  // the value of the top-level "id"
          }
        }
      } else {
        token += c;
      }
      continue;
    }
    if (c == '"') {
      inString = true;
      token.clear();
    } else if (c == '{' || c == '[') {
      depth++;
    } else if (c == '}' || c == ']') {
      if (--depth == 0) return id;  // the object closed: the line was complete
    }
  }
  return std::string();  // truncated: don't trust anything in it
}

bool readState(const std::string& className, ReadingState& state) {
  if (className == "Drowsy") {
    state = ReadingState::Drowsy;
  } else if (className == "Not Drowsy") {
    state = ReadingState::NotDrowsy;
  } else if (className == "No Face") {
    state = ReadingState::NoFace;
  } else {
    return false;
  }
  return true;
}

std::string str(JsonVariantConst v) { return v.is<const char*>() ? v.as<const char*>() : ""; }

}  // namespace

ParseOutcome parsePiRecord(const std::string& rawLine, PiRecord& record) {
  record = PiRecord();
  std::string line = rawLine;
  if (!line.empty() && line.back() == '\r') line.pop_back();

  JsonDocument doc;
  if (line.empty() || deserializeJson(doc, line) != DeserializationError::Ok ||
      !doc.is<JsonObject>()) {
    record.id = topLevelId(line);
    return ParseOutcome::Drop;
  }

  record.id = str(doc["id"]);
  if (record.id.empty()) return ParseOutcome::Drop;

  record.createdAtMs = doc["created_at_ms"].as<uint64_t>();
  if (doc["mono_ms"].is<uint64_t>()) {
    record.hasMonoMs = true;
    record.monoMs = doc["mono_ms"].as<uint64_t>();
  }
  record.bootId = str(doc["boot_id"]);
  if (doc["clock_synced"].is<bool>()) {
    record.hasClockSynced = true;
    record.clockSynced = doc["clock_synced"].as<bool>();
  }

  JsonObjectConst dlq = doc["dlq"];
  if (!dlq.isNull()) {
    record.isDeadLetter = true;
    record.dlqError = str(dlq["error"]);
    record.dlqAttempts = dlq["attempts"] | 0;
    if (dlq["body"].is<JsonObjectConst>()) serializeJson(dlq["body"], record.dlqBody);
  }

  std::string kind = str(doc["kind"]);
  if (kind == "route_status") {
    record.kind = PiRecordKind::RouteStatus;
    return ParseOutcome::Ok;
  }
  if (kind != "drowsiness") return ParseOutcome::Drop;

  record.kind = PiRecordKind::Reading;
  JsonObjectConst payload = doc["payload"];
  record.className = str(payload["class_name"]);
  if (!readState(record.className, record.state)) return ParseOutcome::Drop;

  // Only a face-less reading may come without probabilities (null). Any other reading without
  // both used to read as p(Drowsy)=0, a silent "Not Drowsy".
  JsonVariantConst probabilities = payload["probabilities"];
  if (probabilities.isNull()) {
    if (record.state != ReadingState::NoFace) return ParseOutcome::Drop;
  } else {
    JsonArrayConst p = probabilities.as<JsonArrayConst>();
    if (p.isNull() || p.size() != 2 || !p[0].is<float>() || !p[1].is<float>()) {
      return ParseOutcome::Drop;
    }
    record.hasProbabilities = true;
    record.pNotDrowsy = p[0].as<float>();
    record.pDrowsy = p[1].as<float>();
  }

  record.transition = payload["transition"] | false;
  record.severity = str(payload["severity"]);
  record.previousSeverity = str(payload["previous_severity"]);
  record.reason = str(payload["reason"]);
  record.episode = str(payload["episode"]);
  record.model = str(payload["model"]);
  return ParseOutcome::Ok;
}
