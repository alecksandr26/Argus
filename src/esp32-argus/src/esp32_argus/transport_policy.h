#pragma once

#include <stdint.h>

#include "config.h"

// Mobile data (SIM800L GPRS) first, Wi-Fi as the fallback. Only a *transport* failure
// switches paths -- a server answer, even a 4xx/5xx, proves the GPRS path works. After a GPRS
// failure, Wi-Fi is used for holdOffMs before GPRS is tried again (no flapping per request).
// Pure logic, see test/host/transport_policy_host_test.cpp. Times are millis() values;
// uint32_t so the unsigned subtraction wraps the same way on the host as on the ESP32.

enum class Transport { Gprs, Wifi };

class TransportSelector {
 public:
  explicit TransportSelector(uint32_t holdOffMs) : _holdOffMs(holdOffMs) {}

  Transport pick(uint32_t now) const {
    if (_gprsFailed && now - _gprsFailedAt < _holdOffMs) return Transport::Wifi;
    return Transport::Gprs;
  }

  void report(Transport transport, bool transportOk, uint32_t now) {
    if (transport != Transport::Gprs) return;
    _gprsFailed = !transportOk;
    if (!transportOk) _gprsFailedAt = now;
  }

 private:
  uint32_t _holdOffMs;
  bool _gprsFailed = false;
  uint32_t _gprsFailedAt = 0;
};

// The real deadline of one report attempt (final-tweaks plan section 1, contracts C4): WiFi 8 s;
// mobile data 8 s on an event's first try (and a points-only report), 15 s on a retry or a DLQ
// replay; plus MODEM_SETUP_TIMEOUT_MS when the modem must first be set up (after power-on or a
// lost signal).
struct AttemptTimeouts {
  uint32_t wifiMs = ATTEMPT_TIMEOUT_WIFI_MS;
  uint32_t mobileFirstMs = ATTEMPT_TIMEOUT_MOBILE_FIRST_MS;
  uint32_t mobileRetryMs = ATTEMPT_TIMEOUT_MOBILE_RETRY_MS;
  uint32_t modemSetupMs = MODEM_SETUP_TIMEOUT_MS;
};

inline uint32_t attemptTimeoutMs(const AttemptTimeouts& t, Transport transport, bool retry,
                                 bool modemNeedsSetup) {
  if (transport == Transport::Wifi) return t.wifiMs;
  return (retry ? t.mobileRetryMs : t.mobileFirstMs) + (modemNeedsSetup ? t.modemSetupMs : 0);
}

const char* transportName(Transport transport);
