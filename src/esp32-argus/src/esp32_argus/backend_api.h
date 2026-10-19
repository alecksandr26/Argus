#pragma once

#include <Arduino.h>

#include "fusion.h"

// HTTP client for backend-argus. Field mapping mirrors src/esp32-argus/README.md
// section 2's table and src/backend-argus/app/schemas/alert.py / schemas/status_route.py
// exactly -- see those for the authoritative shapes. Auth: X-Device-Api-Key on every
// request (see backend CLAUDE.md's "Device (ESP32) auth"), using the credentials the Pi hands
// over via CONFIG (credentials.h) -- nothing per-truck is compiled in. The route id comes from
// GET /api/devices/me/active-route, not config.h. A call returns false/!ok without sending
// anything while there are no credentials or no active route; the record then stays unacked
// on the Pi and is retried on a later pull.
//
// Transport: GET/POST go over mobile data (SIM800L GPRS, gprs_http.h) first and fall back to
// WiFi on a transport failure (transport_policy.h); PUT goes over WiFi only, since the
// SIM800L's HTTP stack has no PUT. Over WiFi an https:// backend is verified against the
// pinned root CA (root_ca.h).

// True if at least one transport can carry a request right now (WiFi associated, or the
// SIM800L registered on the mobile network while GPRS isn't in its hold-off).
bool backendIsReachable();

struct AlertPostResult {
  bool ok = false;
  int httpStatus = 0;
  String idAlert;  // backend-assigned AlertOut.id_alert -- needed for related_alert_id /
                    // FusionOrchestrator::recordAlertPosted
};

// isFusionSource=true -> source="fusion": pNotDrowsy/pDrowsy populate ai_metadata.scores,
// gripStatus is required. isFusionSource=false -> source="panic_button": the three
// AI/grip params are ignored and ai_metadata/grip_status are omitted, matching the
// backend's own validate_source_ai_metadata rule.
AlertPostResult backendPostAlert(Severity severity, const String& alertType,
                                  bool isFusionSource, float pNotDrowsy, float pDrowsy,
                                  GripState gripStatus, bool isEscalation,
                                  const String& relatedAlertId, double lat, double lon,
                                  float speedKmh);

// PUT /api/alerts/{id} with {"resolved_at": resolvedAtIso8601} -- the device-key-only
// path (see backend CLAUDE.md: a device key may set resolved_at, never
// reviewed_by_operator/operator_notes). WiFi only.
bool backendResolveAlert(const String& alertId, const String& resolvedAtIso8601);

// POST /api/routes/{active route}/status. odometer has no real sensor on this device (see
// config.h/README.md) -- pass 0.0f, not a fabricated running total.
//
// clientEventId: the Pi record's id, so a retry after a lost response returns the stored row
// (200) instead of inserting a duplicate. occurredAtIso8601: GPS UTC time of this reading, or
// "" to let the backend stamp server time. Returns the HTTP status for relayActionFor()
// (ack_policy.h); <= 0 means nothing reached the backend (transport error, no credentials, or
// no active route yet).
int backendPostRouteStatus(double lat, double lon, float speedKmh, float odometer,
                            Severity vigilance, const String& clientEventId,
                            const String& occurredAtIso8601);
