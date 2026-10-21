#pragma once

#include <Arduino.h>

#include <string>

#include "device_config.h"
#include "pi_record.h"

// The Bluetooth link to the Pi: src/cv-argus/src/sender/protocol.py's grammar, with this device
// as the client (see src/esp32-argus/README.md section 1). The Pi never pushes; this module
// always initiates, with a fresh RFCOMM connection per exchange.

void bluetoothClientBegin();

// Whether the last connection attempt to the Pi succeeded ("the Pi answered the last time we
// asked").
bool bluetoothLinkUp();

// One Serial line on the link: up/down, sessions and records since boot, how long since the Pi
// last answered.
void bluetoothLogStatus();

// What happened to one record, reported back to the Pi after the batch:
// - Ack: delivered to the backend, or nothing to send (a keepalive) -> the Pi marks it sent;
// - Fail: couldn't be delivered -> the Pi keeps it in its dead-letter queue with `reason` (one
//   token: no_credentials, no_network, no_route, no_gps, http_503...) and `body`, the backend
//   message composed here (with the position and time of the event);
// - Skip: not tried this time (a replay stopped at an earlier failure) -> left as it was.
enum class ReportKind : uint8_t { Ack, Fail, Skip };
struct RecordReport {
  ReportKind kind = ReportKind::Ack;
  std::string reason;
  std::string body;
};

// Connects, sends `request` ("PULL <n>" for the live outbox, "DLQ <k>" for dead letters), calls
// `onRecord` for each record (an unreadable one with a known id is ACKed away without calling
// it), reports back (FAIL lines, ACK, DONE) and disconnects. `*dlqWaiting` gets how many dead
// letters the Pi holds. Returns the number of records received (0 is normal), or -1 on a
// connection/protocol failure (then nothing was reported, and the Pi keeps everything as it was).
int bluetoothExchange(const char* request, RecordReport (*onRecord)(const PiRecord& record),
                      int* dlqWaiting);

// Connects to the Pi, asks for this truck's backend credentials (`CONFIG`, see
// device_config.h), and disconnects. Returns the parsed outcome; a connection failure or a
// timeout comes back as Malformed with `out` untouched.
ConfigLineResult bluetoothFetchConfig(DeviceCredentials& out);
