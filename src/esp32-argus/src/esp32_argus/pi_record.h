#pragma once

// One record pulled from the Pi (cv-argus's alerts/serialization.py to_json(), plus a "dlq"
// object on dead letters). Pure parsing with std::string and ArduinoJson, no Arduino or
// hardware calls, so test/host/pi_record_host_test.cpp runs it on the PC.

#include <cstdint>
#include <string>

enum class PiRecordKind : uint8_t { Reading, RouteStatus };
enum class ReadingState : uint8_t { NotDrowsy, Drowsy, NoFace };

// Ok: use it. Drop: this firmware can never use it (unknown kind, bad probabilities,
// unparseable JSON); ACK it away when `id` is known, so it doesn't sit at the head of the Pi's
// queue forever.
enum class ParseOutcome : uint8_t { Ok, Drop };

struct PiRecord {
  std::string id;
  PiRecordKind kind = PiRecordKind::Reading;

  // Readings only.
  ReadingState state = ReadingState::NotDrowsy;
  std::string className;      // payload.class_name
  bool hasProbabilities = false;
  float pNotDrowsy = 0.0f;    // payload.probabilities[0]
  float pDrowsy = 0.0f;       // payload.probabilities[1]
  bool transition = false;    // payload.transition: a class or severity change
  std::string severity;       // payload.severity: none/low/medium/critical ("" if absent)
  std::string previousSeverity;  // payload.previous_severity
  std::string reason;         // payload.reason, e.g. "eyes_closed"
  std::string episode;        // payload.episode: one id from medium until back to none/low
  std::string model;          // payload.model, e.g. "parts-rules-v1"

  // When it happened, on the Pi.
  uint64_t createdAtMs = 0;   // wall clock; trust it only if clockSynced
  bool hasMonoMs = false;
  uint64_t monoMs = 0;
  std::string bootId;
  bool hasClockSynced = false;
  bool clockSynced = false;

  // Dead letters only (served for `DLQ <k>`): why the earlier attempt failed and the backend
  // message composed then (compact JSON, "" if none).
  bool isDeadLetter = false;
  std::string dlqError;
  std::string dlqBody;
  int dlqAttempts = 0;
};

ParseOutcome parsePiRecord(const std::string& line, PiRecord& record);
