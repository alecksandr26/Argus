#include "backend_api.h"

#include <ArduinoJson.h>  // requires ArduinoJson v7+ (JsonDocument's dynamic API)
#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>

#include "config.h"
#include "credentials.h"
#include "gprs_http.h"
#include "modem.h"
#include "root_ca.h"
#include "transport_policy.h"
#include "wifi_client.h"

namespace {

TransportSelector transport(GPRS_HOLD_OFF_MS);

// One of each, reused: the loop is single-threaded and every request ends (http.end()) before
// the next begins. Kept across requests so HTTPClient's keep-alive reuses one TLS session
// instead of paying a full handshake per record.
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

struct HttpResult {
  int status = -1;  // HTTP status, or <= 0 for a transport error / request never sent
  String body;
};

HttpResult sendOverWifi(const char* method, const String& url, const String& body) {
  HttpResult result;
  if (!wifiIsConnected()) {
    Serial.println("[backend] Skipping request -- WiFi not connected");
    return result;
  }

  HTTPClient http;
  http.setReuse(true);
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
    return result;
  }
  // Lambda cold starts behind CloudFront take seconds; the core's 5 s default cuts them off.
  http.setConnectTimeout(BACKEND_HTTP_TIMEOUT_MS);
  http.setTimeout(BACKEND_HTTP_TIMEOUT_MS);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Api-Key", credentials().apiKey);

  result.status = http.sendRequest(method, body);
  if (result.status > 0) result.body = http.getString();
  http.end();
  return result;
}

// Mobile data first for GET/POST, WiFi as the fallback (transport_policy.h); PUT is WiFi-only
// because the SIM800L's AT+HTTPACTION has no PUT.
HttpResult sendRequest(const char* method, const String& path, const String& body) {
  HttpResult result;
  if (!credentialsReady()) {
    Serial.println("[backend] Skipping request -- no device credentials from the Pi yet");
    return result;
  }
  String url = credentials().backendUrl + path;

  bool modemCanDoIt = strcmp(method, "POST") == 0 || strcmp(method, "GET") == 0;
  if (credentials().useGprs() && modemCanDoIt && transport.pick(millis()) == Transport::Gprs) {
    result.status = gprsHttpRequest(method, url, credentials().apiKey, body, &result.body);
    transport.report(Transport::Gprs, result.status > 0, millis());
    if (result.status > 0) return result;
    Serial.println("[backend] Mobile data failed -- falling back to WiFi");
  }
  return sendOverWifi(method, url, body);
}

void logFailure(const char* what, int status) {
  Serial.print("[backend] ");
  Serial.print(what);
  Serial.print(" failed, status=");
  Serial.println(status);
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
  if (!credentialsReady()) return false;
  if (activeRouteTruckId != credentials().truckId) {
    activeRouteTruckId = credentials().truckId;
    activeRouteId = "";
    routeLookupAttempted = false;
  }
  if (activeRouteId.length() > 0) return true;
  if (routeLookupAttempted && millis() - lastRouteLookupAt < ACTIVE_ROUTE_RETRY_MS) return false;
  routeLookupAttempted = true;
  lastRouteLookupAt = millis();

  HttpResult response = sendRequest("GET", "/api/devices/me/active-route", String());
  if (response.status == 200) {
    JsonDocument doc;
    if (deserializeJson(doc, response.body) == DeserializationError::Ok) {
      activeRouteId = doc["id_route"].as<String>();
      Serial.print("[backend] Active route: ");
      Serial.println(activeRouteId);
    }
  } else if (response.status == 404) {
    Serial.println("[backend] No active route for this truck yet -- holding records on the Pi");
  } else {
    logFailure("GET /api/devices/me/active-route", response.status);
    noteStatus(response.status, /*routeScoped=*/false);
  }
  return activeRouteId.length() > 0;
}

}  // namespace

bool backendIsReachable() {
  if (wifiIsConnected()) return true;
  return credentials().useGprs() && transport.pick(millis()) == Transport::Gprs && modemIsRegistered();
}

AlertPostResult backendPostAlert(Severity severity, const String& alertType,
                                  bool isFusionSource, float pNotDrowsy, float pDrowsy,
                                  GripState gripStatus, bool isEscalation,
                                  const String& relatedAlertId, double lat, double lon,
                                  float speedKmh) {
  AlertPostResult result;

  if (!ensureActiveRoute()) return result;

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

  HttpResult response = sendRequest("POST", "/api/alerts", body);
  result.httpStatus = response.status;
  if (response.status == 201) {
    JsonDocument responseDoc;
    if (deserializeJson(responseDoc, response.body) == DeserializationError::Ok) {
      result.idAlert = responseDoc["id_alert"].as<String>();
      result.ok = true;
    } else {
      Serial.println("[backend] POST /api/alerts: couldn't parse the response body");
    }
  } else {
    logFailure("POST /api/alerts", response.status);
    noteStatus(response.status, /*routeScoped=*/true);
  }
  return result;
}

bool backendResolveAlert(const String& alertId, const String& resolvedAtIso8601) {
  JsonDocument doc;
  doc["resolved_at"] = resolvedAtIso8601;
  String body;
  serializeJson(doc, body);

  HttpResult response = sendRequest("PUT", "/api/alerts/" + alertId, body);
  if (response.status != 200) {
    logFailure("PUT /api/alerts/{id}", response.status);
    noteStatus(response.status, /*routeScoped=*/false);
    return false;
  }
  return true;
}

int backendPostRouteStatus(double lat, double lon, float speedKmh, float odometer,
                            Severity vigilance, const String& clientEventId,
                            const String& occurredAtIso8601) {
  if (!ensureActiveRoute()) return 0;  // nothing sent: hold the record on the Pi

  JsonDocument doc;
  JsonObject coordinates = doc["current_coordinates"].to<JsonObject>();
  coordinates["lat"] = lat;
  coordinates["lon"] = lon;
  doc["current_speed"] = speedKmh;
  doc["odometer"] = odometer;
  doc["vigilance"] = severityToString(vigilance);
  if (clientEventId.length() > 0) doc["client_event_id"] = clientEventId;
  if (occurredAtIso8601.length() > 0) doc["occurred_at"] = occurredAtIso8601;

  String body;
  serializeJson(doc, body);

  HttpResult response =
      sendRequest("POST", "/api/routes/" + activeRouteId + "/status", body);
  if (response.status != 200 && response.status != 201) {
    logFailure("POST /api/routes/{id}/status", response.status);
    noteStatus(response.status, /*routeScoped=*/true);
  }
  return response.status;
}
