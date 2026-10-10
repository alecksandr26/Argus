#pragma once

#include <Arduino.h>

// Pulls queued Alert/RouteStatus records off the Pi's Bluetooth SPP server --
// src/cv-argus/src/sender/protocol.py's PULL/ACK grammar, with this device as the client
// (see src/esp32-argus/README.md section 1). The Pi never pushes; this module always
// initiates.

enum class PiRecordKind : uint8_t { DROWSINESS, ROUTE_STATUS };

struct PiRecord {
  String id;
  PiRecordKind kind;
  String className;    // payload.class_name ("Not Drowsy"/"Drowsy") -- drowsiness only
  float pNotDrowsy = 0.0f;  // payload.probabilities[0] -- drowsiness only
  float pDrowsy = 0.0f;     // payload.probabilities[1] -- drowsiness only
};

void bluetoothClientBegin();

// Connects to the Pi, runs one full PULL/ACK exchange, and disconnects.
//
// `onRecord` is called once per row pulled and must return true iff the record was
// successfully relayed onward (e.g. the HTTP POST to backend-argus actually succeeded).
// Only rows `onRecord` returns true for are ACKed -- per the protocol's documented
// invariant (src/esp32-argus/README.md section 1): "Don't ACK unless you've actually
// relayed those records onward ... and gotten a success response." A row onRecord
// returns false for (or that fails to parse) is simply left unacked, so the Pi re-serves
// it verbatim on the next PULL -- duplicates are fine, losing one is not.
//
// Returns the number of rows pulled (0 is normal -- the Pi had nothing queued), or -1 on
// a connection/protocol failure (nothing gets ACKed in that case either).
int bluetoothPullAlerts(bool (*onRecord)(const PiRecord& record));
