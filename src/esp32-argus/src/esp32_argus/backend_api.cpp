#include "backend_api.h"

#include <ArduinoJson.h>  // requires ArduinoJson v7+ (JsonDocument's dynamic API)
#include <HTTPClient.h>

#include "config.h"
#include "wifi_client.h"

namespace {

bool beginRequest(HTTPClient& http, const String& path) {
  if (!wifiIsConnected()) {
    Serial.println("[backend] Skipping request -- WiFi not connected");
    return false;
  }
  http.begin(String(BACKEND_BASE_URL) + path);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Api-Key", DEVICE_API_KEY);
  return true;
}

}  // namespace

AlertPostResult backendPostAlert(Severity severity, const String& alertType,
                                  bool isFusionSource, float pNotDrowsy, float pDrowsy,
                                  GripState gripStatus, bool isEscalation,
                                  const String& relatedAlertId, double lat, double lon,
                                  float speedKmh) {
  AlertPostResult result;

  HTTPClient http;
  if (!beginRequest(http, "/api/alerts")) return result;

  JsonDocument doc;
  doc["id_route"] = ROUTE_ID;
  doc["alert_type"] = alertType;
  doc["severity_level"] = severityToString(severity);
  doc["source"] = isFusionSource ? "fusion" : "panic_button";

  if (isFusionSource) {
    JsonObject aiMetadata = doc["ai_metadata"].to<JsonObject>();
    JsonObject scores = aiMetadata["scores"].to<JsonObject>();
    scores["not_drowsy"] = pNotDrowsy;
    scores["drowsy"] = pDrowsy;
    doc["grip_status"] = (gripStatus == GripState::GOOD) ? "good" : "bad";
  }
  if (isEscalation) {
    doc["related_alert_id"] = relatedAlertId;
  }

  JsonObject coordinates = doc["coordinates"].to<JsonObject>();
  coordinates["lat"] = lat;
  coordinates["lon"] = lon;
  doc["speed_at_event"] = speedKmh;

  String body;
  serializeJson(doc, body);

  int httpStatus = http.POST(body);
  result.httpStatus = httpStatus;
  if (httpStatus == 201) {
    JsonDocument responseDoc;
    if (deserializeJson(responseDoc, http.getString()) == DeserializationError::Ok) {
      result.idAlert = responseDoc["id_alert"].as<String>();
      result.ok = true;
    } else {
      Serial.println("[backend] POST /api/alerts: couldn't parse the response body");
    }
  } else {
    Serial.print("[backend] POST /api/alerts failed, status=");
    Serial.println(httpStatus);
  }
  http.end();
  return result;
}

bool backendResolveAlert(const String& alertId, const String& resolvedAtIso8601) {
  HTTPClient http;
  if (!beginRequest(http, "/api/alerts/" + alertId)) return false;

  JsonDocument doc;
  doc["resolved_at"] = resolvedAtIso8601;
  String body;
  serializeJson(doc, body);

  int httpStatus = http.PUT(body);
  http.end();

  if (httpStatus != 200) {
    Serial.print("[backend] PUT /api/alerts/{id} failed, status=");
    Serial.println(httpStatus);
    return false;
  }
  return true;
}

bool backendPostRouteStatus(double lat, double lon, float speedKmh, float odometer,
                             Severity vigilance) {
  HTTPClient http;
  if (!beginRequest(http, "/api/routes/" + String(ROUTE_ID) + "/status")) return false;

  JsonDocument doc;
  JsonObject coordinates = doc["current_coordinates"].to<JsonObject>();
  coordinates["lat"] = lat;
  coordinates["lon"] = lon;
  doc["current_speed"] = speedKmh;
  doc["odometer"] = odometer;
  doc["vigilance"] = severityToString(vigilance);

  String body;
  serializeJson(doc, body);

  int httpStatus = http.POST(body);
  http.end();

  if (httpStatus != 201) {
    Serial.print("[backend] POST /api/routes/{id}/status failed, status=");
    Serial.println(httpStatus);
    return false;
  }
  return true;
}
