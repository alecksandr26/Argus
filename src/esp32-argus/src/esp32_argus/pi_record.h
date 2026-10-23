#pragma once

// One record pulled from the Pi over Bluetooth protocol v2 (docs/designs/final-tweaks-contracts.md
// C1.4: cv-argus's alerts/serialization.py to_json() plus the serve-time "age_s", and a "dlq"
// object on dead letters). Pure parsing with std::string and ArduinoJson, no Arduino or hardware
// calls, so test/host/pi_record_host_test.cpp runs it on the PC.

#include <cstdint>
#include <string>

enum class ReadingState : uint8_t { NotDrowsy, Drowsy, NoFace };

// Ok: use it. Drop: this firmware can never use it (any kind but "drowsiness" -- v2 has no
// route_status -- bad probabilities, unparseable JSON); ACK it away when `id` is known, so it
// doesn't sit at the head of the Pi's queue forever (C1.4: "The ESP32 ACKs (drops) any row of
// another kind").
enum class ParseOutcome : uint8_t { Ok, Drop };

struct PiRecord {
  std::string id;

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

  // Seconds from the event to the moment the Pi wrote the line (C1.4, computed at serve time).
  // hasAgeS false = null: the Pi can't tell how old it is, so an event is FAILed too_old.
  bool hasAgeS = false;
  float ageS = 0.0f;

  // When it happened, on the Pi.
  uint64_t createdAtMs = 0;   // wall clock; trust it only if clockSynced
  bool hasMonoMs = false;
  uint64_t monoMs = 0;
  std::string bootId;
  bool hasClockSynced = false;
  bool clockSynced = false;

  // Dead letters only (served for `DLQ <k>`): why the last attempt failed, the C2 event object
  // composed when it first failed (compact JSON, "" when the Pi has none, e.g.
  // esp32_unreachable), the failed attempts so far, and how long it has waited in the DLQ.
  bool isDeadLetter = false;
  std::string dlqError;
  std::string dlqBody;
  int dlqAttempts = 0;
  float dlqQueuedS = 0.0f;
};

ParseOutcome parsePiRecord(const std::string& line, PiRecord& record);
