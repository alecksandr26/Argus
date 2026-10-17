#pragma once

#include <Arduino.h>

// SIM800L -- SMS only. Kept as a hardware-direct fallback for the panic button
// specifically: it still works even when WiFi/backend-argus is unreachable, since it
// never touches either. This device's main data transport to backend-argus is WiFi (see
// wifi_client.h/backend_api.h), not this modem's GPRS -- a deliberate project decision,
// not a limitation of the hardware.

void smsBegin();

// Best-effort: waits for GSM network registration (bounded by SMS_NETWORK_WAIT_MS),
// returns false without blocking forever if no network is found. Safe to call again
// later (e.g. retried from the panic handler) if it failed once.
bool smsWaitForNetwork();

// Without a GPS fix the SMS still goes out, but says the location is unavailable instead
// of linking to a meaningless 0,0 (the original prototype refused to send at all).
bool smsSendPanicAlert(bool hasFix, double lat, double lon);
