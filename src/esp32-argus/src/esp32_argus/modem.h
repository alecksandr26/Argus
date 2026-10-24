#pragma once

#include <Arduino.h>

#include "transport_policy.h"

// The SIM800L's UART and raw AT-command helpers for gprs_http (mobile-data HTTP to
// backend-argus) and sms_modem (the critical-alert SMS). Raw AT commands, not TinyGsm: the
// firmware needs a handful of commands, and TinyGsm's general TCP/GPRS layer was mostly
// flash-size overhead.
//
// modemBegin() runs in setup(); after that only the sender task touches the modem (it owns every
// transport to the backend, and sends the SMS between report attempts), so nothing here needs a
// lock. Every wait yields (vTaskDelay(1)) while no byte is waiting, so the other tasks on the core
// keep running.

// Powers the modem on and opens its UART. Call once from setup(). The SIM800L then registers on
// the GSM network by itself (automatic operator selection), with or without a mobile-data APN:
// the SMS needs only that registration.
void modemBegin();

// The current attempt's deadline (transport_policy.h): while one is set, every wait below also
// stops at it, whatever its own timeout -- an idle timeout alone never ends while the modem keeps
// talking. nullptr clears it. The pointed-to Deadline must outlive the attempt.
void modemSetDeadline(const Deadline* deadline);
bool modemDeadlinePassed();

// Drains anything already waiting on the modem UART (stray boot banners, unsolicited
// "RDY"/"+CPIN: READY" lines) so the next exchange starts clean. A "+HTTPACTION:" result among
// the drained bytes (one that arrived after its attempt timed out) is kept for
// modemTakeHttpActionUrc().
void modemFlush();

// True once when a "+HTTPACTION:" URC was seen in drained bytes; `line` gets it.
bool modemTakeHttpActionUrc(String* line = nullptr);

// Reads whatever the modem sends back until `expect` appears, with an *idle* timeout (resets
// every time a byte arrives) -- millis()-rollover-safe -- and the attempt's deadline. Stops early
// on "ERROR" too, so a rejected command doesn't sit out the full timeout.
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

// Like modemReadUntil / modemSendAT, but every byte read also goes through the watch for a late
// "+HTTPACTION:" that modemFlush() keeps (modemTakeHttpActionUrc()), so the result of a request
// whose attempt timed out isn't lost when it arrives in the middle of another exchange (an SMS).
// gprs_http doesn't use these: it waits for that result itself.
bool modemReadUntilKeepingUrcs(const char* expect, unsigned long timeoutMs,
                               String* response = nullptr);
bool modemSendATKeepingUrcs(const String& cmd, const char* expect, unsigned long timeoutMs,
                            String* response = nullptr);

// Raw write, for payloads the modem asked for (HTTP data after "DOWNLOAD", an SMS text after
// '>').
void modemWrite(const String& data);
void modemWriteByte(uint8_t b);  // Ctrl-Z (0x1A) submits an SMS text, ESC (0x1B) cancels it

// Reads exactly n bytes (idle timeout, like modemReadUntil). For length-prefixed payloads such
// as +HTTPREAD, where the body itself may contain "OK"/"ERROR" and must not end the read.
String modemReadBytes(size_t n, unsigned long timeoutMs);

// Polls AT+CREG? once and returns its <stat>: 0 not searching, 1 home, 2 searching, 3 denied,
// 4 unknown, 5 roaming; -1 if the modem didn't answer at all (power, wiring, baud). Updates the
// cached value below.
int modemRegistrationStat();

// The registration state, polled again only when the cached one is older than maxAgeMs (an AT
// round trip per report would cost the sender task time it needs for the report itself).
int modemRegistrationCached(uint32_t maxAgeMs);
void modemForgetRegistration();  // after a transport failure: poll again next time
inline bool modemStatRegistered(int stat) { return stat == 1 || stat == 5; }

// One readable phrase for a modemRegistrationStat() value, for logs.
const char* modemRegistrationText(int stat);
