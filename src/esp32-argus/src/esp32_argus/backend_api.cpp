#include "backend_api.h"

#include <ArduinoJson.h>  // requires ArduinoJson v7+ (JsonDocument's dynamic API)
#include <HTTPClient.h>
#include <WiFiClient.h>
#include <WiFiClientSecure.h>

#include "config.h"
#include "credentials.h"
#include "root_ca.h"
#include "wifi_client.h"

namespace {

// One of each, reused: the loop is single-threaded and every request ends (http.end()) before
// the next begins.
WiFiClient plainClient;
WiFiClientSecure tlsClient;
bool tlsConfigured = false;

// The route this truck is on, from GET /api/devices/me/active-route -- replaces the old
// hardcoded config.h ROUTE_ID. Cleared when the backend says the route is gone (404/409) or
// the credentials now belong to a different truck.
String activeRouteId;
String activeRouteTruckId;
bool routeLookupAttempted = false;
unsigned long lastRouteLookupAt = 0;

bool beginRequest(HTTPClient& http, const String& path) {
  if (!wifiIsConnected()) {
    Serial.println("[backend] Skipping request -- WiFi not connected");
    return false;
  }
  if (!credentialsReady()) {
    Serial.println("[backend] Skipping request -- no device credentials from the Pi yet");
    return false;
  }
  const DeviceCredentials& creds = credentials();
  String url = creds.backendUrl + path;
  bool ok;
  if (url.startsWith("https://")) {
    // AWS (CloudFront) only serves /api/* over HTTPS. The CA is pinned, not setInsecure(): the
    // device key travels in this request's headers.
    if (!tlsConfigured) {
      tlsClient.setCACert(BACKEND_ROOT_CA);
      tlsConfigured = true;
    }
    ok = http.begin(tlsClient, url);
  } else {
    ok = http.begin(plainClient, url);  // LAN bench backend
  }
  if (!ok) {
    Serial.println("[backend] Couldn't start the request -- bad backend URL?");
    return false;
  }
  // Lambda cold starts behind CloudFront take seconds; the core's 5 s default cuts them off.
  http.setConnectTimeout(BACKEND_HTTP_TIMEOUT_MS);
  http.setTimeout(BACKEND_HTTP_TIMEOUT_MS);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Api-Key", creds.apiKey);
  return true;
}

// Reacts to a response status: a 401 means the key was rotated or revoked in the dashboard, so
// ask the Pi for CONFIG again; a 404/409 on a route-scoped write means the route ended.
void noteStatus(int httpStatus, bool routeScoped) {
  if (httpStatus == 401) {
    Serial.println("[backend] 401 -- device key rejected, re-fetching CONFIG from the Pi");
    credentialsMarkStale();
    activeRouteId = "";
    routeLookupAttempted = false;
  } else if (routeScoped && (httpStatus == 404 || httpStatus == 409)) {
    Serial.println("[backend] Route no longer active -- looking it up again");
    activeRouteId = "";
    routeLookupAttempted = false;
  }
}

// Makes sure activeRouteId is set, asking the backend at most once per ACTIVE_ROUTE_RETRY_MS
// while it has none (a whole PULL batch would otherwise mean one lookup per record).
bool ensureActiveRoute() {
  if (activeRouteTruckId != credentials().truckId) {
    activeRouteTruckId = credentials().truckId;
    activeRouteId = "";
    routeLookupAttempted = false;
  }
  if (activeRouteId.length() > 0) return true;
  if (routeLookupAttempted && millis() - lastRouteLookupAt < ACTIVE_ROUTE_RETRY_MS) return false;
  routeLookupAttempted = true;
  lastRouteLookupAt = millis();

  HTTPClient http;
  if (!beginRequest(http, "/api/devices/me/active-route")) return false;
  int httpStatus = http.GET();
  if (httpStatus == 200) {
    JsonDocument doc;
    if (deserializeJson(doc, http.getString()) == DeserializationError::Ok) {
      activeRouteId = doc["id_route"].as<String>();
      Serial.print("[backend] Active route: ");
      Serial.println(activeRouteId);
    }
  } else if (httpStatus == 404) {
    Serial.println("[backend] No active route for this truck yet -- holding records on the Pi");
  } else {
    Serial.print("[backend] GET /api/devices/me/active-route failed, status=");
    Serial.println(httpStatus);
    noteStatus(httpStatus, /*routeScoped=*/false);
  }
  http.end();
  return activeRouteId.length() > 0;
}

}  // namespace

AlertPostResult backendPostAlert(Severity severity, const String& alertType,
                                  bool isFusionSource, float pNotDrowsy, float pDrowsy,
                                  GripState gripStatus, bool isEscalation,
                                  const String& relatedAlertId, double lat, double lon,
                                  float speedKmh) {
  AlertPostResult result;

  if (!ensureActiveRoute()) return result;
  HTTPClient http;
  if (!beginRequest(http, "/api/alerts")) return result;

  JsonDocument doc;
  doc["id_route"] = activeRouteId;
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
    noteStatus(httpStatus, /*routeScoped=*/true);
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
    noteStatus(httpStatus, /*routeScoped=*/false);
    return false;
  }
  return true;
}

bool backendPostRouteStatus(double lat, double lon, float speedKmh, float odometer,
                             Severity vigilance) {
  if (!ensureActiveRoute()) return false;
  HTTPClient http;
  if (!beginRequest(http, "/api/routes/" + activeRouteId + "/status")) return false;

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
    noteStatus(httpStatus, /*routeScoped=*/true);
    return false;
  }
  return true;
}
