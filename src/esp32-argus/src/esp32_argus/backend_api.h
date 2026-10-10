#pragma once

#include <Arduino.h>

#include "fusion.h"

// HTTP client for backend-argus. Field mapping mirrors src/esp32-argus/README.md
// section 2's table and src/backend-argus/app/schemas/alert.py / schemas/status_route.py
// exactly -- see those for the authoritative shapes. Auth: X-Device-Api-Key on every
// request (see backend CLAUDE.md's "Device (ESP32) auth").

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
// reviewed_by_operator/operator_notes).
bool backendResolveAlert(const String& alertId, const String& resolvedAtIso8601);

// POST /api/routes/{ROUTE_ID}/status. odometer has no real sensor on this device (see
// config.h/README.md) -- pass 0.0f, not a fabricated running total.
bool backendPostRouteStatus(double lat, double lon, float speedKmh, float odometer,
                             Severity vigilance);
