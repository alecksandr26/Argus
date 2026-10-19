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
  modemSendAT("AT", "OK", 2000UL);
  modemSendAT("ATE0", "OK", 2000UL);  // echo off, so later reads can't false-match on our own
                                      // echoed command text
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

bool modemIsRegistered() {
  String resp;
  if (!modemSendAT("AT+CREG?", "OK", 2000UL, &resp)) return false;
  CregResult creg;
  return parseCreg(resp.c_str(), creg) && creg.registered();
}
