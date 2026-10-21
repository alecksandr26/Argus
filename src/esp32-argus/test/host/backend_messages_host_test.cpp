// The backend message bodies, composed apart from sending (logged, and kept by the Pi when they
// can't be sent). Field names must match backend-argus's AlertCreate / StatusRouteCreate.
// SOURCES: backend_messages.cpp
// NEEDS: arduinojson
#include <ArduinoJson.h>

#include <string>

#include "backend_messages.h"
#include "check.h"

static GpsSnapshot fix() {
  GpsSnapshot g;
  g.hasFix = true;
  g.lat = 20.6736;
  g.lon = -103.344;
  g.speedKmh = 72.5f;
  return g;
}

static JsonDocument parse(const std::string& body) {
  JsonDocument doc;
  CHECK(deserializeJson(doc, body) == DeserializationError::Ok);
  return doc;
}

static AlertFields critical() {
  AlertFields a;
  a.clientEventId = "rec-1";
  a.alertType = "eyes_closed";
  a.severity = "critical";
  a.model = "parts-rules-v1";
  a.hasScores = true;
  a.pNotDrowsy = 0.05f;
  a.pDrowsy = 0.95f;
  a.occurredAt = "2026-10-20T01:02:03Z";
  return a;
}

static void alertBodyHasTheBackendFields() {
  JsonDocument d = parse(composeAlertBody(critical(), fix()));
  CHECK(d["alert_type"] == "eyes_closed");
  CHECK(d["severity_level"] == "critical");
  CHECK(d["source"] == "fusion");
  CHECK(d["grip_status"] == "good");
  CHECK(d["ai_metadata"]["model"] == "parts-rules-v1");
  CHECK_NEAR(d["ai_metadata"]["scores"]["drowsy"].as<float>(), 0.95, 1e-6);
  CHECK_NEAR(d["coordinates"]["lat"].as<double>(), 20.6736, 1e-6);
  CHECK_NEAR(d["speed_at_event"].as<float>(), 72.5, 1e-6);
  CHECK(d["client_event_id"] == "rec-1");
  CHECK(d["occurred_at"] == "2026-10-20T01:02:03Z");
  CHECK(d["id_route"].isNull());          // added at send time
  CHECK(d["related_alert_id"].isNull());  // not an escalation
}

static void alertWithoutFixHasNoCoordinates() {
  std::string body = composeAlertBody(critical(), GpsSnapshot());
  CHECK(!bodyHasCoordinates(body));
}

static void noOccurredAtMeansServerTime() {
  AlertFields a = critical();
  a.occurredAt = "";
  CHECK(parse(composeAlertBody(a, fix()))["occurred_at"].isNull());
}

static void statusBody() {
  JsonDocument d = parse(composeStatusBody("hb-1", "medium", "", fix()));
  // ArduinoJson keeps a short value like -103.344 as a float internally (lossless in decimal;
  // the wire text is exact), so reading it back as a double is only float-accurate.
  CHECK_NEAR(d["current_coordinates"]["lon"].as<double>(), -103.344, 1e-5);
  CHECK_NEAR(d["current_speed"].as<float>(), 72.5, 1e-6);
  CHECK(d["odometer"].as<double>() == 0.0);
  CHECK(d["vigilance"] == "medium");
  CHECK(d["client_event_id"] == "hb-1");
}

static void resolveBody() {
  CHECK(composeResolveBody("2026-10-20T01:02:03Z") == R"({"resolved_at":"2026-10-20T01:02:03Z"})");
}

static void completeAddsRouteAndLinkButKeepsTheOriginalPosition() {
  std::string body = composeAlertBody(critical(), fix());
  SendTimeFill f;
  f.routeId = "route-9";
  f.relatedAlertId = "alert-1";
  f.gps.hasFix = true;
  f.gps.lat = 1.0;  // a later position must not replace where it happened
  bool usedNow = true;
  CHECK(completeBody(body, f, /*isAlert=*/true, &usedNow));
  CHECK(!usedNow);
  JsonDocument d = parse(body);
  CHECK(d["id_route"] == "route-9");
  CHECK(d["related_alert_id"] == "alert-1");
  CHECK_NEAR(d["coordinates"]["lat"].as<double>(), 20.6736, 1e-6);
}

static void completeKeepsAnExistingLink() {
  AlertFields a = critical();
  a.relatedAlertId = "alert-0";
  std::string body = composeAlertBody(a, fix());
  SendTimeFill f;
  f.relatedAlertId = "alert-1";
  CHECK(completeBody(body, f, true, nullptr));
  CHECK(parse(body)["related_alert_id"] == "alert-0");
}

static void completeFillsAMissingPositionFromTheCurrentFix() {
  std::string body = composeStatusBody("hb", "low", "", GpsSnapshot());
  SendTimeFill f;
  f.gps = fix();
  bool usedNow = false;
  CHECK(completeBody(body, f, /*isAlert=*/false, &usedNow));
  CHECK(usedNow);
  CHECK(bodyHasCoordinates(body));
}

static void completeFailsWithNoPositionAnywhere() {
  std::string body = composeAlertBody(critical(), GpsSnapshot());
  SendTimeFill f;
  CHECK(!completeBody(body, f, true, nullptr));
  std::string notJson = "nope";
  CHECK(!completeBody(notJson, f, true, nullptr));
}

static void isoFormatting() {
  CHECK(isoFromEpochMs(0) == "1970-01-01T00:00:00Z");
  CHECK(isoFromEpochMs(1792264812358ULL) == "2026-10-17T19:20:12Z");
}

int main() {
  RUN(alertBodyHasTheBackendFields);
  RUN(alertWithoutFixHasNoCoordinates);
  RUN(noOccurredAtMeansServerTime);
  RUN(statusBody);
  RUN(resolveBody);
  RUN(completeAddsRouteAndLinkButKeepsTheOriginalPosition);
  RUN(completeKeepsAnExistingLink);
  RUN(completeFillsAMissingPositionFromTheCurrentFix);
  RUN(completeFailsWithNoPositionAnywhere);
  RUN(isoFormatting);
  return report("backend_messages_host_test");
}
