#pragma once

#include <Arduino.h>

// SIM800L panic SMS -- a hardware-direct fallback for the panic button specifically: it
// still goes out even when backend-argus is unreachable over both mobile data and WiFi. The
// same modem also carries mobile-data HTTP (gprs_http.h); both share modem.h's UART.
//
// Raw AT commands (modem.h), not TinyGsm -- this module's usage is narrow enough (power-on,
// poll network registration, send one SMS) that the full TinyGsm library was pure flash-size
// overhead for what it bought here.

void smsBegin();

// Best-effort: waits for GSM network registration (bounded by SMS_NETWORK_WAIT_MS),
// returns false without blocking forever if no network is found. Safe to call again
// later (e.g. retried from the panic handler) if it failed once.
bool smsWaitForNetwork();

// Without a GPS fix the SMS still goes out, but says the location is unavailable instead
// of linking to a meaningless 0,0 (the original prototype refused to send at all).
bool smsSendPanicAlert(bool hasFix, double lat, double lon);
