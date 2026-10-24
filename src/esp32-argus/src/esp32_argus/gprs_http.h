#pragma once

#include <Arduino.h>

#include "device_config.h"
#include "transport_policy.h"

// HTTP(S) POST to backend-argus over the SIM800L's mobile data (GPRS), using the modem's own
// HTTP + SSL stack (AT+HTTP*), so the TLS session runs on the modem, not on the ESP32.
//
// Why this works against the AWS deployment: the SIM800L's SSL is old (TLS 1.0, no SNI), and
// CloudFront's default *.cloudfront.net certificate accepts exactly that handshake (TLS 1.0,
// AES128-SHA, no SNI -- checked with openssl against the live distribution). It would stop
// working on a custom domain with a TLS 1.2-only security policy. The modem does NOT verify
// the server certificate, so this link is encrypted but not authenticated; the WiFi path
// (backend_api.cpp) is the one that pins Amazon Root CA 1.
//
// The only request this firmware makes is the report POST (C2). Only the sender task calls this.

enum class GprsEnd : uint8_t {
  Response,        // the server answered: `status` is its HTTP status
  TransportError,  // not registered, no bearer, an AT step failed, or a SIM800L 6xx code
  Timeout,         // the attempt's deadline passed
  Preempted,       // stopped between AT steps for a higher event (before the request was fired)
  ModemBusy,       // an earlier attempt's request still holds the modem's HTTP session: this
                   // attempt fails, but not as a transport failure (no WiFi hold-off, the bearer
                   // and the registration stay as they were)
};

// Composes the request body when called. gprsPostJson calls it once, right before AT+HTTPDATA,
// so every age in it is measured after registration, bearer setup and the other AT steps
// (contracts C2.1 [sync 3]).
typedef String (*GprsBodyComposer)();

struct GprsResult {
  GprsEnd end = GprsEnd::TransportError;
  int status = -1;
  String body;
};

// POSTs the body `compose` returns as JSON with the X-Device-Api-Key header. `deadline` bounds
// the whole attempt (every AT step's wait is capped by what is left of it). `preempt` is asked
// between AT steps (and between the short waits for an earlier request's late result), never
// after AT+HTTPACTION fired the request; true stops the attempt (GprsEnd::Preempted).
GprsResult gprsPostJson(const String& url, const DeviceCredentials& creds,
                        GprsBodyComposer compose, const Deadline& deadline, bool (*preempt)());

// True while the data bearer isn't known to be open (after power-on, or after a lost signal or
// a failed attempt): the next attempt also gets MODEM_SETUP_TIMEOUT_MS (attemptTimeoutMs()).
bool gprsNeedsSetup();

// Between attempts (the sender task's idle loop): picks up the "+HTTPACTION:" of a request whose
// attempt timed out after it was fired, logs what the server answered, and frees the modem's HTTP
// session. Doesn't block.
void gprsPoll();
