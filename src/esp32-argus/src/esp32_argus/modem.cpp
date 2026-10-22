#include "modem.h"

#include "config.h"
#include "modem_parse.h"

namespace {
HardwareSerial modemSerial(1);
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

void modemFlush() {
  while (modemSerial.available()) modemSerial.read();
}

bool modemReadUntil(const char* expect, unsigned long timeoutMs, String* response) {
  String buf;
  unsigned long lastByteAt = millis();
  while (millis() - lastByteAt < timeoutMs) {
    while (modemSerial.available()) {
      buf += (char)modemSerial.read();
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

bool modemSendAT(const String& cmd, const char* expect, unsigned long timeoutMs,
                 String* response) {
  modemFlush();
  modemSerial.print(cmd);
  modemSerial.print("\r\n");
  return modemReadUntil(expect, timeoutMs, response);
}

bool modemSendATWithin(const String& cmd, const char* expect, unsigned long totalMs,
                       String* response) {
  modemFlush();
  modemSerial.print(cmd);
  modemSerial.print("\r\n");
  String buf;
  unsigned long sentAt = millis();
  while (millis() - sentAt < totalMs) {
    while (modemSerial.available()) {
      buf += (char)modemSerial.read();
      if (buf.indexOf(expect) != -1 || buf.indexOf("ERROR") != -1) {
        if (response) *response = buf;
        return buf.indexOf(expect) != -1;
      }
    }
    delay(10);
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
  while (buf.length() < n && millis() - lastByteAt < timeoutMs) {
    while (modemSerial.available() && buf.length() < n) {
      buf += (char)modemSerial.read();
      lastByteAt = millis();
    }
  }
  return buf;
}

int modemRegistrationStat() {
  String resp;
  if (!modemSendAT("AT+CREG?", "OK", 2000UL, &resp)) return -1;
  CregResult creg;
  return parseCreg(resp.c_str(), creg) ? creg.stat : -1;
}

bool modemIsRegistered() {
  int stat = modemRegistrationStat();
  return stat == 1 || stat == 5;
}

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
