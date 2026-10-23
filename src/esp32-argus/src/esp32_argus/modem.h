#pragma once

#include <Arduino.h>

// The SIM800L's UART and raw AT-command helpers for gprs_http (mobile-data HTTP to
// backend-argus). Raw AT commands, not TinyGsm: the firmware needs a handful of commands, and
// TinyGsm's general TCP/GPRS layer was mostly flash-size overhead.

// Powers the modem on and opens its UART. Call once from setup().
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

// Like modemSendAT, but with a hard deadline from the moment the command is sent instead of an
// idle timeout. For slow network commands (AT+SAPBR=1,1): a modem that keeps talking -- e.g.
// rebooting in a loop and printing RDY / Call Ready -- would reset an idle timeout forever.
bool modemSendATWithin(const String& cmd, const char* expect, unsigned long totalMs,
                       String* response = nullptr);

// A modem reply on one line for logs: CR/LF runs become " | ", ends trimmed.
String modemOneLine(const String& response);

// Raw write, for payloads the modem asked for (HTTP data after "DOWNLOAD").
void modemWrite(const String& data);

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
