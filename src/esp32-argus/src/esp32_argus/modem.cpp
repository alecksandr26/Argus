#include "modem.h"

#include "config.h"
#include "modem_parse.h"

namespace {
HardwareSerial modemSerial(1);

const Deadline* attemptDeadline = nullptr;

// What modemFlush() threw away, scanned for a late "+HTTPACTION:" result.
String flushedTail;
bool sawHttpActionUrc = false;
String httpActionUrc;

int cachedStat = -1;
bool statKnown = false;
unsigned long statAt = 0;

bool pastDeadline() { return attemptDeadline && attemptDeadline->expired(millis()); }

void noteFlushed(char c) {
  flushedTail += c;
  if (flushedTail.length() > 96) flushedTail.remove(0, flushedTail.length() - 96);
  int at = flushedTail.indexOf("+HTTPACTION:");
  if (at != -1 && flushedTail.indexOf('\n', at) != -1) {
    int end = flushedTail.indexOf('\n', at);
    httpActionUrc = flushedTail.substring(at, end);
    httpActionUrc.trim();
    sawHttpActionUrc = true;
    flushedTail = "";
  }
}
}  // namespace

void modemBegin() {
  pinMode(MODEM_PWKEY, OUTPUT);
  pinMode(MODEM_RST, OUTPUT);
  pinMode(MODEM_POWER_ON, OUTPUT);

  digitalWrite(MODEM_PWKEY, LOW);
  digitalWrite(MODEM_RST, HIGH);
  digitalWrite(MODEM_POWER_ON, HIGH);

  modemSerial.begin(9600, SERIAL_8N1, MODEM_RX, MODEM_TX);
  delay(3000);

  // The modem is often still settling right after power-on and misses the first poke.
  modemSendAT("AT", "OK", 2000UL);
  bool answering = modemSendAT("AT", "OK", 2000UL);
  modemSendAT("ATE0", "OK", 2000UL);  // echo off, so later reads can't false-match on our own
                                      // echoed command text
  Serial.print("[modem] ");
  Serial.println(answering ? "SIM800L answering" : "SIM800L not answering on its UART (power? wiring?)");
  if (answering) {
    Serial.print("[modem] Network: ");
    Serial.println(modemRegistrationText(modemRegistrationStat()));
  }
}

void modemSetDeadline(const Deadline* deadline) { attemptDeadline = deadline; }

bool modemDeadlinePassed() { return pastDeadline(); }

void modemFlush() {
  while (modemSerial.available()) noteFlushed((char)modemSerial.read());
}

bool modemTakeHttpActionUrc(String* line) {
  modemFlush();
  if (!sawHttpActionUrc) return false;
  sawHttpActionUrc = false;
  if (line) *line = httpActionUrc;
  return true;
}

namespace {
// modemReadUntil(), optionally passing every byte through the late-"+HTTPACTION:" watch too.
bool readUntil(const char* expect, unsigned long timeoutMs, String* response, bool keepUrcs) {
  String buf;
  unsigned long lastByteAt = millis();
  while (millis() - lastByteAt < timeoutMs && !pastDeadline()) {
    if (!modemSerial.available()) {
      vTaskDelay(1);  // nothing yet: let the other tasks run
      continue;
    }
    while (modemSerial.available()) {
      char c = (char)modemSerial.read();
      buf += c;
      if (keepUrcs) noteFlushed(c);
      lastByteAt = millis();
      if (buf.indexOf(expect) != -1) {
        if (response) *response = buf;
        return true;
      }
      if (buf.indexOf("ERROR") != -1) {
        if (response) *response = buf;
        return false;
      }
    }
  }
  if (response) *response = buf;
  return false;
}

bool sendAT(const String& cmd, const char* expect, unsigned long timeoutMs, String* response,
            bool keepUrcs) {
  modemFlush();
  modemSerial.print(cmd);
  modemSerial.print("\r\n");
  return readUntil(expect, timeoutMs, response, keepUrcs);
}
}  // namespace

bool modemReadUntil(const char* expect, unsigned long timeoutMs, String* response) {
  return readUntil(expect, timeoutMs, response, /*keepUrcs=*/false);
}

bool modemReadUntilKeepingUrcs(const char* expect, unsigned long timeoutMs, String* response) {
  return readUntil(expect, timeoutMs, response, /*keepUrcs=*/true);
}

bool modemSendAT(const String& cmd, const char* expect, unsigned long timeoutMs,
                 String* response) {
  return sendAT(cmd, expect, timeoutMs, response, /*keepUrcs=*/false);
}

bool modemSendATKeepingUrcs(const String& cmd, const char* expect, unsigned long timeoutMs,
                            String* response) {
  return sendAT(cmd, expect, timeoutMs, response, /*keepUrcs=*/true);
}

bool modemSendATWithin(const String& cmd, const char* expect, unsigned long totalMs,
                       String* response) {
  modemFlush();
  modemSerial.print(cmd);
  modemSerial.print("\r\n");
  String buf;
  unsigned long sentAt = millis();
  while (millis() - sentAt < totalMs && !pastDeadline()) {
    while (modemSerial.available()) {
      buf += (char)modemSerial.read();
      if (buf.indexOf(expect) != -1 || buf.indexOf("ERROR") != -1) {
        if (response) *response = buf;
        return buf.indexOf(expect) != -1;
      }
    }
    delay(10);  // vTaskDelay underneath: yields
  }
  if (response) *response = buf;
  return false;
}

String modemOneLine(const String& response) {
  String out;
  bool gap = false;
  for (size_t i = 0; i < response.length(); i++) {
    char c = response[i];
    if (c == '\r' || c == '\n') {
      gap = true;
      continue;
    }
    if (gap && out.length() > 0) out += " | ";
    gap = false;
    out += c;
  }
  return out;
}

void modemWrite(const String& data) { modemSerial.print(data); }

void modemWriteByte(uint8_t b) { modemSerial.write(b); }

String modemReadBytes(size_t n, unsigned long timeoutMs) {
  String buf;
  buf.reserve(n);
  unsigned long lastByteAt = millis();
  while (buf.length() < n && millis() - lastByteAt < timeoutMs && !pastDeadline()) {
    if (!modemSerial.available()) {
      vTaskDelay(1);
      continue;
    }
    while (modemSerial.available() && buf.length() < n) {
      buf += (char)modemSerial.read();
      lastByteAt = millis();
    }
  }
  return buf;
}

int modemRegistrationStat() {
  String resp;
  int stat = -1;
  CregResult creg;
  // Polled between requests too (before a report, while an SMS waits), when a timed-out request's
  // late "+HTTPACTION:" may arrive: kept for gprs_http rather than read past.
  if (modemSendATKeepingUrcs("AT+CREG?", "OK", 2000UL, &resp) && parseCreg(resp.c_str(), creg)) {
    stat = creg.stat;
  }
  cachedStat = stat;
  statKnown = true;
  statAt = millis();
  return stat;
}

int modemRegistrationCached(uint32_t maxAgeMs) {
  if (statKnown && millis() - statAt < maxAgeMs) return cachedStat;
  return modemRegistrationStat();
}

void modemForgetRegistration() { statKnown = false; }

const char* modemRegistrationText(int stat) {
  switch (stat) {
    case -1: return "the SIM800L doesn't answer (power? wiring?)";
    case 0: return "not registered and not searching (SIM missing or locked?)";
    case 1: return "registered (home network)";
    case 2: return "not registered, still searching for the network (antenna? 2G coverage?)";
    case 3: return "registration denied by the network";
    case 5: return "registered (roaming)";
    default: return "registration state unknown";
  }
}
