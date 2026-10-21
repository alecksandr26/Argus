#pragma once

// The JSON bodies this device sends to backend-argus, composed apart from sending them: the
// firmware logs every message it would send, and when it can't send one it hands the exact
// message (with the GPS position and time of the event) back to the Pi, which keeps it in its
// dead-letter queue. Field names mirror src/backend-argus/app/schemas/alert.py and
// schemas/status_route.py. Pure (std::string + ArduinoJson), so
// test/host/backend_messages_host_test.cpp runs it on the PC.

#include <cstdint>
#include <string>

struct GpsSnapshot {
  bool hasFix = false;
  double lat = 0.0;
  double lon = 0.0;
  float speedKmh = 0.0f;
};

struct AlertFields {
  std::string clientEventId;  // the Pi record id: a retry returns the stored alert, no duplicate
  std::string alertType;      // the Pi's reason (e.g. "eyes_closed"), else its class
  std::string severity;       // backend Severity: low/medium/critical
  std::string model;          // ai_metadata.model, e.g. "parts-rules-v1"
  bool hasScores = false;
  float pNotDrowsy = 0.0f;
  float pDrowsy = 0.0f;
  std::string relatedAlertId;  // an escalation's earlier alert, if known now
  std::string occurredAt;      // ISO-8601 UTC, or "" for server time
};

// POST /api/alerts, without id_route (only known once the active route is looked up) and
// without coordinates when there's no GPS fix. source "fusion" with grip_status "good": a
// placeholder until the grip sensor exists (the backend requires one for a camera alert).
std::string composeAlertBody(const AlertFields& alert, const GpsSnapshot& gps);

// The panic button's POST /api/alerts: an unconditional critical with no camera or grip data
// (source "panic_button" must come without ai_metadata/grip_status).
std::string composePanicAlertBody(const std::string& clientEventId, const std::string& occurredAt,
                                  const GpsSnapshot& gps);

// POST /api/routes/{id}/status. odometer is always 0: no sensor for it on this device.
std::string composeStatusBody(const std::string& clientEventId, const std::string& vigilance,
                              const std::string& occurredAt, const GpsSnapshot& gps);

// POST /api/alerts/{id}/resolve (the POST twin of PUT, which the SIM800L can't send).
std::string composeResolveBody(const std::string& resolvedAt);

bool bodyHasCoordinates(const std::string& body);

// Fills in what's only known at send time, without touching what the body already has (a dead
// letter keeps its original position): id_route on an alert, related_alert_id if it has none,
// and the position from `gps` if it has none (*usedCurrentGps tells the caller to log that).
// Returns false if the body isn't a JSON object or still has no position.
struct SendTimeFill {
  std::string routeId;         // alerts only; "" leaves it out
  std::string relatedAlertId;  // alerts only
  GpsSnapshot gps;
};
bool completeBody(std::string& body, const SendTimeFill& fill, bool isAlert,
                  bool* usedCurrentGps);

// Epoch milliseconds -> "YYYY-MM-DDTHH:MM:SSZ".
std::string isoFromEpochMs(uint64_t epochMs);
