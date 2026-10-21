#pragma once

#include <Arduino.h>

// The SIM800L's UART and raw AT-command helpers, shared by sms_module (panic SMS) and
// gprs_http (mobile-data HTTP to backend-argus). Raw AT commands, not TinyGsm -- see
// sms_module.cpp for why. Everything runs from loop(), so the two users never interleave.

// Powers the modem on and opens its UART. Call once from setup(), before smsBegin().
void modemBegin();

// Drains anything already waiting on the modem UART (stray boot banners, unsolicited
// "RDY"/"+CPIN: READY" lines) so the next exchange starts clean.
void modemFlush();

// Reads whatever the modem sends back until `expect` appears, with an *idle* timeout (resets
// every time a byte arrives) -- millis()-rollover-safe. Stops early on "ERROR" too, so a
// rejected command doesn't sit out the full timeout.
bool modemReadUntil(const char* expect, unsigned long timeoutMs, String* response = nullptr);

// Sends `cmd` + CRLF and waits for `expect`.
bool modemSendAT(const String& cmd, const char* expect, unsigned long timeoutMs,
                 String* response = nullptr);

// Raw write, for payloads the modem asked for (an SMS body after '>', HTTP data after
// "DOWNLOAD").
void modemWrite(const String& data);
void modemWriteByte(uint8_t b);

// Reads exactly n bytes (idle timeout, like modemReadUntil). For length-prefixed payloads such
// as +HTTPREAD, where the body itself may contain "OK"/"ERROR" and must not end the read.
String modemReadBytes(size_t n, unsigned long timeoutMs);

// Polls AT+CREG? once. True if registered on the home network or roaming.
bool modemIsRegistered();

// Polls AT+CREG? once and returns its <stat>: 0 not searching, 1 home, 2 searching, 3 denied,
// 4 unknown, 5 roaming; -1 if the modem didn't answer at all (power, wiring, baud).
int modemRegistrationStat();

// One readable phrase for a modemRegistrationStat() value, for logs.
const char* modemRegistrationText(int stat);
