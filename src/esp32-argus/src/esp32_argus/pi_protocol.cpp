#include "pi_protocol.h"

#include <cstdlib>
#include <cstring>

namespace {

std::string trimCr(const std::string& line) {
  if (!line.empty() && line.back() == '\r') return line.substr(0, line.size() - 1);
  return line;
}

// A non-negative decimal integer filling the whole of `text`.
bool parseCount(const std::string& text, int& n) {
  if (text.empty() || text.size() > 9) return false;
  for (char c : text) {
    if (c < '0' || c > '9') return false;
  }
  n = std::atoi(text.c_str());
  return true;
}

std::vector<std::string> splitSpaces(const std::string& line) {
  std::vector<std::string> parts;
  size_t at = 0;
  while (at <= line.size()) {
    size_t end = line.find(' ', at);
    if (end == std::string::npos) end = line.size();
    parts.push_back(line.substr(at, end - at));
    at = end + 1;
  }
  return parts;
}

bool isTokenChar(char c) {
  return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') ||
         c == '_' || c == '.' || c == ':' || c == '-';
}

void addIdLines(std::vector<ScriptLine>& script, const char* verb, const char* reply,
                const std::vector<std::string>& ids) {
  for (size_t from = 0; from < ids.size(); from += kIdsPerLine) {
    std::string line = verb;
    line += ' ';
    for (size_t i = from; i < ids.size() && i < from + kIdsPerLine; i++) {
      if (i > from) line += ',';
      line += ids[i];
    }
    script.push_back({line, reply});
  }
}

}  // namespace

bool parseEndLine(const std::string& rawLine, EndLine& out) {
  out = EndLine();
  std::string line = trimCr(rawLine);
  if (line == "END") return true;
  if (line.compare(0, 4, "END ") != 0) return false;
  std::vector<std::string> parts = splitSpaces(line.substr(4));
  if (parts.empty() || parts.size() > 2) return false;
  if (!parseCount(parts[0], out.dlqWaiting)) return false;
  if (parts.size() == 2 && !parseCount(parts[1], out.outboxWaiting)) return false;
  return true;
}

bool parseCountReply(const std::string& rawLine, const char* word, int& n) {
  std::string line = trimCr(rawLine);
  size_t len = std::strlen(word);
  if (line.size() < len + 2 || line.compare(0, len, word) != 0 || line[len] != ' ') return false;
  return parseCount(line.substr(len + 1), n);
}

bool parseOkLine(const std::string& rawLine, SessionSummary& out) {
  out = SessionSummary();
  std::string line = trimCr(rawLine);
  if (line != "OK" && line.compare(0, 3, "OK ") != 0) return false;
  if (line == "OK") return true;
  for (const std::string& part : splitSpaces(line.substr(3))) {
    size_t eq = part.find('=');
    if (eq == std::string::npos) continue;
    std::string key = part.substr(0, eq);
    int value = 0;
    if (!parseCount(part.substr(eq + 1), value)) continue;
    if (key == "acked") out.acked = value;
    else if (key == "failed") out.failed = value;
    else if (key == "leased") out.leased = value;
    else if (key == "dlq") out.dlq = value;
    else if (key == "outbox") out.outbox = value;
  }
  return true;
}

bool isReasonToken(const std::string& reason) {
  if (reason.empty()) return false;
  for (char c : reason) {
    if (!isTokenChar(c)) return false;
  }
  return true;
}

std::string reasonToken(const std::string& reason) {
  if (reason.empty()) return "error";
  std::string out = reason;
  for (char& c : out) {
    if (!isTokenChar(c)) c = '_';
  }
  return out;
}

std::string pullRequest(int n) { return "PULL " + std::to_string(n < 0 ? 0 : n); }

std::string dlqRequest() { return "DLQ 1"; }

std::string formatFailLine(const std::string& id, const std::string& reason,
                           const std::string& body) {
  std::string line = "FAIL " + id + " " + reasonToken(reason) + " ";
  if (body.empty() || body.find('\n') != std::string::npos ||
      body.find('\r') != std::string::npos) {
    line += "-";  // a body must be one line of JSON; anything else is not sent
  } else {
    line += body;
  }
  return line;
}

std::vector<ScriptLine> buildReportScript(const std::vector<std::string>& acks,
                                          const std::vector<FailReport>& fails,
                                          const std::vector<std::string>& taken) {
  std::vector<ScriptLine> script;
  for (const FailReport& f : fails) script.push_back({formatFailLine(f.id, f.reason, f.body), ""});
  addIdLines(script, "ACK", "ACKED", acks);
  addIdLines(script, "TAKEN", "LEASED", taken);
  script.push_back({"DONE", "OK"});
  return script;
}
