#pragma once

// Parsers for the SIM800L responses the GPRS transport depends on. Pure C string parsing (no
// Arduino String) so test/host/modem_parse_host_test.cpp runs on the PC. Each parser finds its
// prefix anywhere in `response`, so a raw buffer with echoes/blank lines before it is fine.

struct ClbsFix {
  bool ok = false;
  double lat = 0.0;
  double lon = 0.0;
  int accuracyM = 0;
};

struct HttpActionResult {
  int method = -1;
  int status = 0;
  int length = 0;
  // 6xx are the SIM800L's own codes (601 network error, 603 DNS, 604 stack busy, ...), not a
  // server answer -- the request never got a real HTTP response.
  bool isTransportError() const { return status >= 600 && status < 700; }
};

struct CregResult {
  int n = 0;
  int stat = 0;
  bool registered() const { return stat == 1 || stat == 5; }  // home or roaming
};

// "+CLBS: <code>[,<lon>,<lat>,<accuracy>]" -- longitude FIRST. Returns false if the line isn't
// a well-formed +CLBS; fix.ok is false for a non-zero location code or a 0,0 position.
bool parseClbs(const char* response, ClbsFix& fix);

// "+HTTPACTION: <method>,<status>,<length>"
bool parseHttpAction(const char* response, HttpActionResult& result);

// "+CREG: <n>,<stat>"
bool parseCreg(const char* response, CregResult& result);
