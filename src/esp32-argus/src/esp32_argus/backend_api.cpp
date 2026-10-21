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

BackendResult sendOverWifi(const char* method, const String& url, const String& body) {
  BackendResult result;
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

// Mobile data first, WiFi as the fallback (transport_policy.h).
BackendResult sendRequest(const char* method, const String& path, const String& body) {
  BackendResult result;
  if (!credentialsReady()) return result;
  String url = credentials().backendUrl + path;

  if (credentials().useGprs() && transport.pick(millis()) == Transport::Gprs) {
    result.status = gprsHttpRequest(method, url, credentials().apiKey, body, &result.body);
    transport.report(Transport::Gprs, result.status > 0, millis());
    if (result.status > 0) return result;
    Serial.println("[backend] Mobile data failed -- falling back to WiFi");
  }
  return sendOverWifi(method, url, body);
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

  BackendResult response = sendRequest("GET", "/api/devices/me/active-route", String());
  if (response.status == 200) {
    JsonDocument doc;
    if (deserializeJson(doc, response.body) == DeserializationError::Ok) {
      activeRouteId = doc["id_route"].as<String>();
      Serial.print("[backend] Active route: ");
      Serial.println(activeRouteId);
    }
  } else if (response.status == 404) {
    Serial.println("[backend] No active route for this truck yet -- start its trip in the "
                   "dashboard");
  } else {
    Serial.print("[backend] GET /api/devices/me/active-route failed, status=");
    Serial.println(response.status);
    backendNoteStatus(response.status, /*routeScoped=*/false);
  }
  return activeRouteId.length() > 0;
}

}  // namespace

// Prints why there's no network, at most every NET_STATUS_LOG_INTERVAL_MS.
static void logNoNetwork(const char* why) {
  static bool logged = false;
  static unsigned long lastAt = 0;
  if (logged && millis() - lastAt < NET_STATUS_LOG_INTERVAL_MS) return;
  logged = true;
  lastAt = millis();
  Serial.print("[net] No network: ");
  Serial.println(why);
}

bool backendIsReachable() {
  if (wifiIsConnected()) return true;
  if (!credentials().useGprs()) {
    logNoNetwork("no WiFi, and no mobile data APN from the Pi (ARGUS_GPRS_APN)");
    return false;
  }
  if (transport.pick(millis()) != Transport::Gprs) {
    logNoNetwork("mobile data failed recently (on hold for GPRS_HOLD_OFF_MS) and no WiFi");
    return false;
  }
  int stat = modemRegistrationStat();
  if (stat == 1 || stat == 5) return true;
  String why = String("no WiFi, and mobile data unusable: ") + modemRegistrationText(stat);
  logNoNetwork(why.c_str());
  return false;
}

const char* backendBlockReason(BackendBlock block) {
  switch (block) {
    case BackendBlock::Ready: return "ready";
    case BackendBlock::NoCredentials: return "no_credentials";
    case BackendBlock::NoNetwork: return "no_network";
    case BackendBlock::NoRoute: return "no_route";
  }
  return "?";
}

const char* backendBlockHint(BackendBlock block) {
  switch (block) {
    case BackendBlock::Ready: return "ready";
    case BackendBlock::NoCredentials: return "no argus-device.env on the Pi yet";
    case BackendBlock::NoNetwork: return "no mobile data and no WiFi";
    case BackendBlock::NoRoute: return "no trip in progress for this truck";
  }
  return "?";
}

BackendBlock backendReadiness() {
  if (!credentialsReady()) return BackendBlock::NoCredentials;
  wifiEnsureConnected();
  if (!backendIsReachable()) return BackendBlock::NoNetwork;
  if (!ensureActiveRoute()) return BackendBlock::NoRoute;
  return BackendBlock::Ready;
}

String backendActiveRouteId() { return activeRouteId; }

BackendResult backendPost(const String& path, const String& body) {
  Serial.print("[backend] -> POST ");
  Serial.print(path);
  Serial.print(" ");
  Serial.println(body);
  BackendResult result = sendRequest("POST", path, body);
  Serial.print("[backend] <- ");
  if (result.status > 0) {
    Serial.print(result.status);
    Serial.print(" ");
    Serial.println(result.body.substring(0, 160));
  } else {
    Serial.println("no answer (transport error)");
  }
  return result;
}

void backendNoteStatus(int httpStatus, bool routeScoped) {
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
