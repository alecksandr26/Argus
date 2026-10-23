#pragma once

#include <Arduino.h>

#include <string>
#include <vector>

#include "device_config.h"
#include "pi_protocol.h"

// The Bluetooth link to the Pi: protocol v2 (docs/designs/final-tweaks-contracts.md C1), with
// this device as the client and a fresh RFCOMM connection per session. The Pi never pushes. This
// module only does the I/O: what to do with each record and which report lines to send is decided
// by the pure modules (pull_session.h, pi_protocol.h) in the sketch, in between
// bluetoothOpenSession() and bluetoothCloseSession(). Nothing here talks to the backend: a session
// takes well under a second, far inside the Pi's 30 s wait for the report lines. Only the loop
// task calls into this module.

void bluetoothClientBegin();

// Whether the last connection attempt to the Pi succeeded ("the Pi answered the last time we
// asked").
bool bluetoothLinkUp();

// One Serial line on the link: up/down, sessions and records since boot, how long since the Pi
// last answered, free heap.
void bluetoothLogStatus();

// Connects, sends `request` (pullRequest(n) or dlqRequest()) and reads every record line up to
// `END <dlq> <outbox>`. The whole batch is read before any of it is handled: BluetoothSerial's
// receive queue is 512 bytes and silently drops what doesn't fit, so handling records while the
// Pi still streams loses bytes. False on no link, a timeout, a malformed END or running out of
// memory: then the connection is closed and nothing was reported (the Pi keeps every row as it
// was and serves it again).
bool bluetoothOpenSession(const std::string& request, std::vector<std::string>& lines,
                          EndLine& end);

// Writes the report lines (buildReportScript()) and reads each reply (ACKED / LEASED, then OK),
// then disconnects. True when the Pi's closing OK arrived (`summary` filled). Lines written
// before a failure took effect on the Pi already (C1.2 [sync 2]); the caller keeps its outcomes
// until an OK and resends them, which is safe.
bool bluetoothCloseSession(const std::vector<ScriptLine>& script, SessionSummary& summary);

// Drops an open session without reporting (e.g. the sketch ran out of memory handling it): the Pi
// leaves every unreported row where it was.
void bluetoothDisconnect();

// Connects to the Pi, asks for this truck's backend credentials (`CONFIG`, see
// device_config.h), and disconnects. Returns the parsed outcome; a connection failure or a
// timeout comes back as Malformed with `out` untouched.
ConfigLineResult bluetoothFetchConfig(DeviceCredentials& out);
