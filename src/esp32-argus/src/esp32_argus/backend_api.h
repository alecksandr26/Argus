#pragma once

#include <Arduino.h>

// Sending to backend-argus. What to send is composed elsewhere (backend_messages.h); this file
// only knows whether a message can go out right now, and sends it. Auth: X-Device-Api-Key on
// every request (see backend CLAUDE.md's "Device (ESP32) auth"), using the credentials the Pi
// hands over via CONFIG (credentials.h) -- nothing per-truck is compiled in. The route id
// comes from GET /api/devices/me/active-route, not config.h.
//
// Transport: GET/POST go over mobile data (SIM800L GPRS, gprs_http.h) first and fall back to
// WiFi on a transport failure (transport_policy.h). Every call this firmware makes is a GET or
// a POST (resolving uses POST /api/alerts/{id}/resolve, since the SIM800L can't send PUT).
// Over WiFi an https:// backend is verified against the pinned root CA (root_ca.h).

// True if at least one transport can carry a request right now (WiFi associated, or the
// SIM800L registered on the mobile network while GPRS isn't in its hold-off).
bool backendIsReachable();

// Why a message can't be sent right now, checked in this order. The token (backendBlockReason)
// is what the ESP32 reports to the Pi in `FAIL <id> <reason>`.
enum class BackendBlock : uint8_t { Ready, NoCredentials, NoNetwork, NoRoute };
const char* backendBlockReason(BackendBlock block);  // "no_credentials", "no_network", ...
const char* backendBlockHint(BackendBlock block);    // a human explanation for the log

// Credentials from the Pi, a transport, and this truck's active route (looked up at most once
// per ACTIVE_ROUTE_RETRY_MS while there's none).
BackendBlock backendReadiness();
String backendActiveRouteId();  // "" until looked up

struct BackendResult {
  int status = -1;  // HTTP status, or <= 0 when nothing reached the backend
  String body;
};

// POSTs `body` to `path` (e.g. "/api/alerts"), logging the request and the answer. Doesn't
// check backendReadiness(): the caller does, so it can report why.
BackendResult backendPost(const String& path, const String& body);

// Reacts to a response: a 401 means the key was rotated or revoked in the dashboard, so ask the
// Pi for CONFIG again; a 404/409 on a route-scoped write means the route ended.
void backendNoteStatus(int httpStatus, bool routeScoped);
