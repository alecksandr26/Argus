// The client side of Bluetooth protocol v2 (docs/designs/final-tweaks-contracts.md C1.2/C1.3):
// the lines the ESP32 writes and the Pi replies it parses.
// SOURCES: pi_protocol.cpp
#include <string>
#include <vector>

#include "check.h"
#include "pi_protocol.h"

static void endLineCarriesBothCounts() {
  EndLine e;
  CHECK(parseEndLine("END 3 5", e));
  CHECK(e.dlqWaiting == 3);
  CHECK(e.outboxWaiting == 5);
  CHECK(parseEndLine("END 0 0\r", e));
  CHECK(e.dlqWaiting == 0 && e.outboxWaiting == 0);
}

static void olderEndLinesParseWithZeroCounts() {
  EndLine e;
  CHECK(parseEndLine("END", e));
  CHECK(e.dlqWaiting == 0 && e.outboxWaiting == 0);
  CHECK(parseEndLine("END 7", e));
  CHECK(e.dlqWaiting == 7 && e.outboxWaiting == 0);
}

static void malformedEndLinesAreNotEnd() {
  EndLine e;
  CHECK(!parseEndLine(R"({"id":"x"})", e));  // a record line
  CHECK(!parseEndLine("END x 1", e));
  CHECK(!parseEndLine("END 1 2 3", e));
  CHECK(!parseEndLine("END -1 0", e));
  CHECK(!parseEndLine("ENDING", e));
  CHECK(!parseEndLine("", e));
}

static void countReplies() {
  int n = -1;
  CHECK(parseCountReply("ACKED 3", "ACKED", n) && n == 3);
  CHECK(parseCountReply("LEASED 0\r", "LEASED", n) && n == 0);
  CHECK(!parseCountReply("ACKED", "ACKED", n));
  CHECK(!parseCountReply("ACKED x", "ACKED", n));
  CHECK(!parseCountReply("LEASED 2", "ACKED", n));
  CHECK(!parseCountReply("ACKEDX 2", "ACKED", n));
}

static void okLine() {
  SessionSummary s;
  CHECK(parseOkLine("OK acked=3 failed=1 leased=2 dlq=4 outbox=5", s));
  CHECK(s.acked == 3 && s.failed == 1 && s.leased == 2 && s.dlq == 4 && s.outbox == 5);
  CHECK(parseOkLine("OK", s));
  CHECK(s.acked == 0);
  CHECK(parseOkLine("OK acked=1 future=9", s));  // unknown keys ignored
  CHECK(s.acked == 1);
  CHECK(!parseOkLine("OKAY", s));
  CHECK(!parseOkLine("ACKED 1", s));
}

static void reasonTokens() {
  CHECK(isReasonToken("http_503"));
  CHECK(isReasonToken("route_finished"));
  CHECK(isReasonToken("a.b:c-d"));
  CHECK(!isReasonToken(""));
  CHECK(!isReasonToken("two words"));
  CHECK(reasonToken("two words") == "two_words");
  CHECK(reasonToken("") == "error");
  CHECK(reasonToken("no_network") == "no_network");
}

static void requests() {
  CHECK(pullRequest(8) == "PULL 8");
  CHECK(dlqRequest() == "DLQ 1");  // MAX_DLQ_LIMIT is 1 in v2
}

static void failLines() {
  CHECK(formatFailLine("abc", "http_503", "") == "FAIL abc http_503 -");
  CHECK(formatFailLine("abc", "no_network", R"({"kind":"alert"})") ==
        R"(FAIL abc no_network {"kind":"alert"})");
  CHECK(formatFailLine("abc", "bad reason", "") == "FAIL abc bad_reason -");
  CHECK(formatFailLine("abc", "x", "{\"a\":\n1}") == "FAIL abc x -");  // never a 2nd line
}

static void reportScriptOrderAndReplies() {
  std::vector<std::string> acks = {"a1", "a2"};
  std::vector<FailReport> fails = {{"f1", "retry_window", ""}, {"f2", "no_network", "{}"}};
  std::vector<std::string> taken = {"t1"};
  std::vector<ScriptLine> s = buildReportScript(acks, fails, taken);
  CHECK(s.size() == 5);
  CHECK(s[0].line == "FAIL f1 retry_window -" && s[0].expectReply.empty());
  CHECK(s[1].line == "FAIL f2 no_network {}" && s[1].expectReply.empty());
  CHECK(s[2].line == "ACK a1,a2" && s[2].expectReply == "ACKED");
  CHECK(s[3].line == "TAKEN t1" && s[3].expectReply == "LEASED");
  CHECK(s[4].line == "DONE" && s[4].expectReply == "OK");
}

static void emptyReportIsJustDone() {
  std::vector<ScriptLine> s = buildReportScript({}, {}, {});
  CHECK(s.size() == 1);
  CHECK(s[0].line == "DONE");
}

static void longIdListsAreChunked() {
  std::vector<std::string> ids;
  for (int i = 0; i < kIdsPerLine + 3; i++) ids.push_back("i" + std::to_string(i));
  std::vector<ScriptLine> s = buildReportScript(ids, {}, ids);
  CHECK(s.size() == 5);  // 2 ACK + 2 TAKEN + DONE
  CHECK(s[0].line.compare(0, 7, "ACK i0,") == 0);
  CHECK(s[1].line == "ACK i12,i13,i14");
  CHECK(s[3].line == "TAKEN i12,i13,i14");
}

int main() {
  RUN(endLineCarriesBothCounts);
  RUN(olderEndLinesParseWithZeroCounts);
  RUN(malformedEndLinesAreNotEnd);
  RUN(countReplies);
  RUN(okLine);
  RUN(reasonTokens);
  RUN(requests);
  RUN(failLines);
  RUN(reportScriptOrderAndReplies);
  RUN(emptyReportIsJustDone);
  RUN(longIdListsAreChunked);
  return report("pi_protocol_host_test");
}
