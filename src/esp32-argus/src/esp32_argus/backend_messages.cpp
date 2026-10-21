#include "backend_messages.h"

#include <ArduinoJson.h>  // requires ArduinoJson v7+
#include <ctime>

namespace {

std::string serialize(const JsonDocument& doc) {
  std::string out;
  serializeJson(doc, out);
  return out;
}

void putOccurredAt(JsonDocument& doc, const std::string& occurredAt) {
  // Absent = the backend stamps server time; time_source follows from occurred_at.
  if (!occurredAt.empty()) doc["occurred_at"] = occurredAt;
}

}  // namespace

std::string composeAlertBody(const AlertFields& a, const GpsSnapshot& gps) {
  JsonDocument doc;
  doc["alert_type"] = a.alertType;
  doc["severity_level"] = a.severity;
  doc["source"] = "fusion";
  JsonObject meta = doc["ai_metadata"].to<JsonObject>();
  if (!a.model.empty()) meta["model"] = a.model;
  JsonObject scores = meta["scores"].to<JsonObject>();
  if (a.hasScores) {
    scores["not_drowsy"] = a.pNotDrowsy;
    scores["drowsy"] = a.pDrowsy;
  }
  doc["grip_status"] = "good";  // placeholder: no grip sensor yet
  if (!a.relatedAlertId.empty()) doc["related_alert_id"] = a.relatedAlertId;
  if (gps.hasFix) {
    JsonObject c = doc["coordinates"].to<JsonObject>();
    c["lat"] = gps.lat;
    c["lon"] = gps.lon;
    doc["speed_at_event"] = gps.speedKmh;
  }
  if (!a.clientEventId.empty()) doc["client_event_id"] = a.clientEventId;
  putOccurredAt(doc, a.occurredAt);
  return serialize(doc);
}

std::string composePanicAlertBody(const std::string& clientEventId, const std::string& occurredAt,
                                  const GpsSnapshot& gps) {
  JsonDocument doc;
  doc["alert_type"] = "panic_button";
  doc["severity_level"] = "critical";
  doc["source"] = "panic_button";
  if (gps.hasFix) {
    JsonObject c = doc["coordinates"].to<JsonObject>();
    c["lat"] = gps.lat;
    c["lon"] = gps.lon;
    doc["speed_at_event"] = gps.speedKmh;
  }
  if (!clientEventId.empty()) doc["client_event_id"] = clientEventId;
  putOccurredAt(doc, occurredAt);
  return serialize(doc);
}

std::string composeStatusBody(const std::string& clientEventId, const std::string& vigilance,
                              const std::string& occurredAt, const GpsSnapshot& gps) {
  JsonDocument doc;
  if (gps.hasFix) {
    JsonObject c = doc["current_coordinates"].to<JsonObject>();
    c["lat"] = gps.lat;
    c["lon"] = gps.lon;
    doc["current_speed"] = gps.speedKmh;
  }
  doc["odometer"] = 0.0;
  doc["vigilance"] = vigilance;
  if (!clientEventId.empty()) doc["client_event_id"] = clientEventId;
  putOccurredAt(doc, occurredAt);
  return serialize(doc);
}

std::string composeResolveBody(const std::string& resolvedAt) {
  JsonDocument doc;
  doc["resolved_at"] = resolvedAt;
  return serialize(doc);
}

bool bodyHasCoordinates(const std::string& body) {
  JsonDocument doc;
  if (deserializeJson(doc, body) != DeserializationError::Ok) return false;
  return doc["coordinates"].is<JsonObject>() || doc["current_coordinates"].is<JsonObject>();
}

bool completeBody(std::string& body, const SendTimeFill& fill, bool isAlert,
                  bool* usedCurrentGps) {
  if (usedCurrentGps) *usedCurrentGps = false;
  JsonDocument doc;
  if (deserializeJson(doc, body) != DeserializationError::Ok || !doc.is<JsonObject>()) {
    return false;
  }
  const char* positionKey = isAlert ? "coordinates" : "current_coordinates";
  if (isAlert) {
    if (!fill.routeId.empty()) doc["id_route"] = fill.routeId;
    if (!doc["related_alert_id"].is<const char*>() && !fill.relatedAlertId.empty()) {
      doc["related_alert_id"] = fill.relatedAlertId;
    }
  }
  if (!doc[positionKey].is<JsonObject>()) {
    if (!fill.gps.hasFix) return false;
    JsonObject c = doc[positionKey].to<JsonObject>();
    c["lat"] = fill.gps.lat;
    c["lon"] = fill.gps.lon;
    doc[isAlert ? "speed_at_event" : "current_speed"] = fill.gps.speedKmh;
    if (usedCurrentGps) *usedCurrentGps = true;
  }
  body = serialize(doc);
  return true;
}

std::string isoFromEpochMs(uint64_t epochMs) {
  time_t seconds = (time_t)(epochMs / 1000ULL);
  struct tm utc;
  gmtime_r(&seconds, &utc);
  char buf[24];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &utc);
  return buf;
}
