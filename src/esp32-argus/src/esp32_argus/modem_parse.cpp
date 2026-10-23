#include "modem_parse.h"

#include <stdlib.h>
#include <string.h>

namespace {

bool atFieldEnd(const char* p) {
  return *p == ',' || *p == '\0' || *p == '\r' || *p == '\n';
}

// Strict number parsers: the whole field up to the next ',' or line end must be numeric.
bool parseDoubleField(const char*& p, double& out) {
  char* end = nullptr;
  out = strtod(p, &end);
  if (end == p || !atFieldEnd(end)) return false;
  p = end;
  return true;
}

bool parseIntField(const char*& p, int& out) {
  char* end = nullptr;
  long v = strtol(p, &end, 10);
  if (end == p || !atFieldEnd(end)) return false;
  out = (int)v;
  p = end;
  return true;
}

bool skipComma(const char*& p) {
  if (*p != ',') return false;
  p++;
  return true;
}

const char* afterPrefix(const char* response, const char* prefix) {
  const char* p = strstr(response, prefix);
  if (!p) return nullptr;
  p += strlen(prefix);
  while (*p == ' ') p++;
  return p;
}

}  // namespace

bool parseClbs(const char* response, ClbsFix& fix) {
  fix = ClbsFix();
  const char* p = afterPrefix(response, "+CLBS:");
  if (!p) return false;
  int code = 0;
  if (!parseIntField(p, code)) return false;
  if (code != 0) return true;  // well-formed, but no fix
  double lon = 0.0, lat = 0.0;
  int accuracy = 0;
  if (!skipComma(p) || !parseDoubleField(p, lon)) return false;
  if (!skipComma(p) || !parseDoubleField(p, lat)) return false;
  if (!skipComma(p) || !parseIntField(p, accuracy)) return false;
  fix.lat = lat;
  fix.lon = lon;
  fix.accuracyM = accuracy;
  fix.ok = !(lat == 0.0 && lon == 0.0);
  return true;
}

bool parseHttpAction(const char* response, HttpActionResult& result) {
  result = HttpActionResult();
  const char* p = afterPrefix(response, "+HTTPACTION:");
  if (!p) return false;
  return parseIntField(p, result.method) && skipComma(p) && parseIntField(p, result.status) &&
         skipComma(p) && parseIntField(p, result.length);
}

bool parseCreg(const char* response, CregResult& result) {
  result = CregResult();
  const char* p = afterPrefix(response, "+CREG:");
  if (!p) return false;
  return parseIntField(p, result.n) && skipComma(p) && parseIntField(p, result.stat);
}

LateAction lateActionVerdict(bool resultArrived, bool preempted, uint32_t sinceFiredMs,
                             uint32_t giveUpAfterMs) {
  if (resultArrived) return LateAction::Free;
  if (preempted) return LateAction::Preempted;
  if (sinceFiredMs >= giveUpAfterMs) return LateAction::Free;
  return LateAction::Busy;
}

uint32_t lateActionChunkMs(uint32_t attemptLeftMs, uint32_t chunkMs, uint32_t reserveMs) {
  if (attemptLeftMs <= reserveMs) return 0;
  uint32_t usable = attemptLeftMs - reserveMs;
  return usable < chunkMs ? usable : chunkMs;
}
