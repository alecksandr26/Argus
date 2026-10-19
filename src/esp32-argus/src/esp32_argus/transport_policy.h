#pragma once

#include <stdint.h>

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
