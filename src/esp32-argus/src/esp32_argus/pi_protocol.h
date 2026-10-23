#pragma once

// The client side of the Pi <-> ESP32 Bluetooth protocol v2 (docs/designs/final-tweaks-contracts.md
// C1.2/C1.3): the lines this device writes and the Pi's replies it reads. No I/O here --
// bluetooth_client.cpp does the reading and writing -- so test/host/pi_protocol_host_test.cpp runs
// it on the PC. v2 is not compatible with v1 and there is no version negotiation.
//
//   C: PULL <n> | DLQ 1                       (or a report session: first line ACK/FAIL)
//   S: <record JSON line> x0..n
//   S: END <dlq_waiting> <outbox_waiting>
//   C: FAIL <id> <reason> <body>  (no reply)
//   C: ACK <ids>   -> S: ACKED <n>
//   C: TAKEN <ids> -> S: LEASED <n>
//   C: DONE        -> S: OK acked=<a> failed=<f> leased=<l> dlq=<d> outbox=<o>

#include <string>
#include <vector>

// `END <dlq_waiting> <outbox_waiting>`. A bare `END` or `END <n>` (an older Pi) parses too, with
// the missing counts 0.
struct EndLine {
  int dlqWaiting = 0;
  int outboxWaiting = 0;
};
bool parseEndLine(const std::string& line, EndLine& out);

// `<word> <n>`, e.g. "ACKED 3" or "LEASED 2".
bool parseCountReply(const std::string& line, const char* word, int& n);

// The Pi's last line: `OK acked=.. failed=.. leased=.. dlq=.. outbox=..` (missing keys stay 0).
struct SessionSummary {
  int acked = 0;
  int failed = 0;
  int leased = 0;
  int dlq = 0;
  int outbox = 0;
};
bool parseOkLine(const std::string& line, SessionSummary& out);

// A FAIL reason is one token `[A-Za-z0-9_.:-]+` (the Pi's _FAIL_RE). reasonToken() makes any
// string one: other characters become '_', an empty one becomes "error".
bool isReasonToken(const std::string& reason);
std::string reasonToken(const std::string& reason);

std::string pullRequest(int n);  // "PULL <n>"
std::string dlqRequest();        // "DLQ 1": the Pi serves at most one dead letter per session

// `FAIL <id> <reason> <body>`: `body` is compact JSON (one line) or empty, sent as "-".
std::string formatFailLine(const std::string& id, const std::string& reason,
                           const std::string& body);

// The report lines of one session, in the order they are written, each with the reply it waits
// for ("" = none): FAILs, then ACK (chunked), then TAKEN (chunked), then DONE (waits for "OK").
struct ScriptLine {
  std::string line;
  std::string expectReply;  // "", "ACKED", "LEASED" or "OK"
};
struct FailReport {
  std::string id;
  std::string reason;
  std::string body;
};
// At most this many ids per ACK/TAKEN line, so no line grows past a few hundred bytes.
constexpr int kIdsPerLine = 12;
std::vector<ScriptLine> buildReportScript(const std::vector<std::string>& acks,
                                          const std::vector<FailReport>& fails,
                                          const std::vector<std::string>& taken);
