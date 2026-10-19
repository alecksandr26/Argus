#pragma once

#include <Arduino.h>

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
// GET and POST only: the SIM800L's AT+HTTPACTION has no PUT, so PUT /api/alerts/{id}
// (resolving an incident) can only go over WiFi -- see backend_api.cpp.

// `method` is "GET" or "POST" (`body` is ignored for GET). `apiKey` goes out as the
// X-Device-Api-Key header. Returns the HTTP status, or -1 for a transport failure (no
// registration, no GPRS bearer, an AT command failed, or a SIM800L 6xx code). `responseBody`
// gets the body when non-null.
int gprsHttpRequest(const char* method, const String& url, const String& apiKey,
                    const String& body, String* responseBody);
