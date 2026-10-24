// The one backend message, POST /api/devices/me/report (docs/designs/final-tweaks-contracts.md
// C2): composed apart from sending (logged, and the event object kept by the Pi when a critical
// can't be delivered), and its 200 answer parsed. Field names must match backend-argus's report
// schema.
// SOURCES: backend_messages.cpp pi_event_policy.cpp
// NEEDS: arduinojson
#include <ArduinoJson.h>

#include <string>

#include "backend_messages.h"
#include "check.h"
#include "config.h"

static JsonDocument parse(const std::string& body) {
  JsonDocument doc;
  CHECK(deserializeJson(doc, body) == DeserializationError::Ok);
  return doc;
}

static PiRecord criticalRecord() {
  PiRecord r;
  r.id = "3f2a9c";
  r.className = "Drowsy";
  r.transition = true;
  r.severity = "critical";
  r.previousSeverity = "medium";
  r.episode = "a1b2c3d4e5f6";
  r.reason = "eyes_closed";
  r.model = "parts-rules-v1";
  r.hasProbabilities = true;
  r.pNotDrowsy = 0.05f;
  r.pDrowsy = 0.95f;
  r.hasAgeS = true;
  r.ageS = 1.6f;
  return r;
}

static EventPosition here(bool lastKnown = false, float ageS = 0.0f) {
  EventPosition p;
  p.known = true;
  p.lat = 20.6597;
  p.lon = -103.3496;
  p.lastKnown = lastKnown;
  p.locationAgeS = ageS;
  return p;
}

static void criticalAlertEventHasTheC2Fields() {
  ReportEvent ev = eventFromRecord(criticalRecord(), PiEventKind::AlertCritical, here(), true, 62.5f);
  std::string body = composeEventJson(ev, 1.64f, EventDelivery::Live, 0);
  // The wire text is exact; ArduinoJson reads a short decimal back as a float, so the read-back
  // checks below are only float-accurate (1e-5 degrees, about 1 m).
  CHECK(body.find(R"("lat":20.6597,"lon":-103.3496)") != std::string::npos);
  JsonDocument d = parse(body);
  CHECK(d["client_event_id"] == "3f2a9c");
  CHECK(d["episode"] == "a1b2c3d4e5f6");
  CHECK(d["kind"] == "alert");
  CHECK(d["severity"] == "critical");
  CHECK(d["alert_type"] == "eyes_closed");
  CHECK_NEAR(d["age_s"].as<float>(), 1.6, 1e-4);
  CHECK(d["delivery"] == "live");
  CHECK(d["queued_seconds"].as<float>() == 0.0f);
  CHECK_NEAR(d["position"]["lat"].as<double>(), 20.6597, 1e-5);
  CHECK_NEAR(d["position"]["lon"].as<double>(), -103.3496, 1e-5);
  CHECK(d["position"]["last_known"] == false);
  CHECK(d["position"]["location_age_s"].as<float>() == 0.0f);
  CHECK_NEAR(d["speed"].as<float>(), 62.5, 1e-4);
  CHECK(d["ai_metadata"]["model"] == "parts-rules-v1");
  CHECK_NEAR(d["ai_metadata"]["scores"]["drowsy"].as<float>(), 0.95, 1e-6);
  CHECK_NEAR(d["ai_metadata"]["scores"]["not_drowsy"].as<float>(), 0.05, 1e-6);
  // Nothing absolute and nothing of the retired endpoints.
  CHECK(d["occurred_at"].isNull());
  CHECK(d["id_route"].isNull());
  CHECK(d["grip_status"].isNull());
  CHECK(d["related_alert_id"].isNull());
}

static void mediumAlertAndAReasonlessRecord() {
  PiRecord r = criticalRecord();
  r.severity = "medium";
  r.previousSeverity = "none";
  r.reason = "";
  ReportEvent ev = eventFromRecord(r, PiEventKind::AlertMedium, here(), true, 0);
  JsonDocument d = parse(composeEventJson(ev, 0, EventDelivery::Live, 0));
  CHECK(d["severity"] == "medium");
  CHECK(d["alert_type"] == "Drowsy");  // no reason: the class (never an empty alert_type)
}

static void driverOkEventHasNoSeverity() {
  PiRecord r = criticalRecord();
  r.severity = "none";
  r.previousSeverity = "critical";
  r.reason = "ok";
  ReportEvent ev = eventFromRecord(r, PiEventKind::DriverOk, here(), true, 50);
  JsonDocument d = parse(composeEventJson(ev, 0.2f, EventDelivery::Live, 0));
  CHECK(d["kind"] == "ok");
  CHECK(d["severity"].isNull());
  CHECK(d["alert_type"].isNull());
  CHECK(d["episode"] == "a1b2c3d4e5f6");  // matched to its alerts by (route, episode)
}

static void missingEpisodeFallsBackToTheRecordId() {
  PiRecord r = criticalRecord();
  r.episode = "";
  ReportEvent ev = eventFromRecord(r, PiEventKind::AlertCritical, EventPosition(), false, 0);
  CHECK(ev.episode == "3f2a9c");
}

static void unknownPositionAndSpeedAreNull() {
  PiRecord r = criticalRecord();
  r.hasProbabilities = false;  // a No Face critical has no scores
  r.model = "";
  ReportEvent ev = eventFromRecord(r, PiEventKind::AlertCritical, EventPosition(), false, 0);
  JsonDocument d = parse(composeEventJson(ev, 3, EventDelivery::Live, 0));
  CHECK(d["position"].isNull());
  CHECK(!d["position"].isUnbound());  // present, as null: "position unknown"
  CHECK(d["speed"].isNull());
  CHECK(d["ai_metadata"].isNull());
}

static void scoresWithoutModelKeepTheModelNull() {
  PiRecord r = criticalRecord();
  r.model = "";
  ReportEvent ev = eventFromRecord(r, PiEventKind::AlertCritical, here(), true, 1);
  JsonDocument d = parse(composeEventJson(ev, 0, EventDelivery::Live, 0));
  CHECK(d["ai_metadata"]["model"].isNull());
  CHECK(d["ai_metadata"]["scores"]["drowsy"].is<float>());
}

static void lastKnownPositionIsLabelled() {
  ReportEvent ev = eventFromRecord(criticalRecord(), PiEventKind::AlertCritical,
                                   here(true, 125.04f), false, 0);
  JsonDocument d = parse(composeEventJson(ev, 0, EventDelivery::Live, 0));
  CHECK(d["position"]["last_known"] == true);
  CHECK_NEAR(d["position"]["location_age_s"].as<float>(), 125.0, 1e-4);
}

static void storedEventRoundTripsForADlqReplay() {
  // The FAIL body is the composed event; a replay re-reads it and only the per-attempt fields
  // change (age_s, delivery, queued_seconds), C1.8.
  ReportEvent ev = eventFromRecord(criticalRecord(), PiEventKind::AlertCritical,
                                   here(true, 12), true, 62.5f);
  std::string stored = composeEventJson(ev, 1.6f, EventDelivery::Live, 0);
  ReportEvent back;
  CHECK(eventFromStoredJson(stored, back));
  JsonDocument d = parse(composeEventJson(back, 95.3f, EventDelivery::Dlq, 37.0f));
  CHECK(d["client_event_id"] == "3f2a9c");
  CHECK(d["episode"] == "a1b2c3d4e5f6");
  CHECK(d["severity"] == "critical");
  CHECK(d["alert_type"] == "eyes_closed");
  CHECK_NEAR(d["age_s"].as<float>(), 95.3, 1e-4);
  CHECK(d["delivery"] == "dlq");
  CHECK_NEAR(d["queued_seconds"].as<float>(), 37.0, 1e-4);
  CHECK_NEAR(d["position"]["lat"].as<double>(), 20.6597, 1e-5);  // where it happened
  CHECK(d["position"]["last_known"] == true);
  CHECK_NEAR(d["position"]["location_age_s"].as<float>(), 12.0, 1e-4);
  CHECK_NEAR(d["speed"].as<float>(), 62.5, 1e-4);
  CHECK(d["ai_metadata"]["model"] == "parts-rules-v1");
}

static void unusableStoredEventsAreRefused() {
  ReportEvent out;
  CHECK(!eventFromStoredJson("", out));
  CHECK(!eventFromStoredJson("nope", out));
  CHECK(!eventFromStoredJson("[]", out));
  CHECK(!eventFromStoredJson(R"({"kind":"alert","severity":"critical","alert_type":"x"})", out));
  CHECK(!eventFromStoredJson(
      R"({"client_event_id":"a","episode":"e","kind":"alert","severity":"low","alert_type":"x"})",
      out));
  CHECK(!eventFromStoredJson(
      R"({"client_event_id":"a","episode":"e","kind":"panic","severity":"critical","alert_type":"x"})",
      out));
  // The retired POST /api/alerts body a v1 firmware may have left in the DLQ.
  CHECK(!eventFromStoredJson(
      R"({"alert_type":"eyes_closed","severity_level":"critical","source":"fusion","client_event_id":"a"})",
      out));
}

static void legacyBodiesKeepTheirPosition() {
  // A dead letter queued by the v1 firmware stored the retired POST /api/alerts body.
  EventPosition p;
  bool hasSpeed = false;
  float speed = 0;
  CHECK(legacyPositionFromBody(
      R"({"alert_type":"eyes_closed","coordinates":{"lat":19.5,"lon":-99.25},"speed_at_event":40})",
      p, hasSpeed, speed));
  CHECK(p.known && !p.lastKnown);
  CHECK_NEAR(p.lat, 19.5, 1e-6);
  CHECK(hasSpeed);
  CHECK_NEAR(speed, 40.0, 1e-6);
  CHECK(!legacyPositionFromBody(R"({"alert_type":"eyes_closed"})", p, hasSpeed, speed));
  CHECK(!p.known);
  CHECK(!legacyPositionFromBody("", p, hasSpeed, speed));
}

static TrackPoint point(uint32_t atMs, const char* vigilance, bool lastKnown = false) {
  TrackPoint t;
  t.atMs = atMs;
  t.lat = 20.5 + atMs / 1e6;
  t.lon = -103.25;
  t.speedKmh = 61.26f;
  t.vigilance = vigilance;
  t.lastKnown = lastKnown;
  t.locationAgeS = lastKnown ? 30.0f : 0.0f;
  return t;
}

static void reportCarriesPointsOldestFirstWithRelativeAges() {
  TrackPoint pts[3] = {point(1000, "low"), point(2000, "medium"), point(3000, "unknown", true)};
  JsonDocument d = parse(composeReportBody(pts, 3, 5000, nullptr, 0, EventDelivery::Live, 0));
  JsonArray p = d["points"];
  CHECK(p.size() == 3);
  CHECK_NEAR(p[0]["age_s"].as<float>(), 4.0, 1e-4);
  CHECK_NEAR(p[2]["age_s"].as<float>(), 2.0, 1e-4);
  CHECK(p[0]["vigilance"] == "low");
  CHECK(p[1]["vigilance"] == "medium");
  CHECK_NEAR(p[0]["speed"].as<float>(), 61.3, 1e-4);
  CHECK(p[0]["last_known"] == false);
  CHECK(p[0]["location_age_s"].as<float>() == 0.0f);
  CHECK(p[2]["last_known"] == true);
  CHECK(p[2]["speed"].as<float>() == 0.0f);  // 0 when last_known (C2.1)
  CHECK_NEAR(p[2]["location_age_s"].as<float>(), 30.0, 1e-4);
  CHECK(d["event"].isNull());
}

static void reportKeepsOnlyTheNewestTenPoints() {
  TrackPoint pts[13];
  for (int i = 0; i < 13; i++) pts[i] = point(1000 * (i + 1), "low");
  JsonDocument d = parse(composeReportBody(pts, 13, 14000, nullptr, 0, EventDelivery::Live, 0));
  JsonArray p = d["points"];
  CHECK(p.size() == REPORT_MAX_POINTS);
  CHECK_NEAR(p[0]["age_s"].as<float>(), 10.0, 1e-4);  // points 4..13
  CHECK_NEAR(p[9]["age_s"].as<float>(), 1.0, 1e-4);
}

static void emptyReportStillHasAPointsArray() {
  JsonDocument d = parse(composeReportBody(nullptr, 0, 0, nullptr, 0, EventDelivery::Live, 0));
  CHECK(d["points"].is<JsonArray>());
  CHECK(d["points"].size() == 0);
}

static void reportWithAnEvent() {
  TrackPoint pts[1] = {point(1000, "critical")};
  ReportEvent ev = eventFromRecord(criticalRecord(), PiEventKind::AlertCritical, here(), true, 60);
  JsonDocument d = parse(composeReportBody(pts, 1, 1500, &ev, 2.0f, EventDelivery::Live, 0));
  CHECK(d["points"].size() == 1);
  CHECK(d["event"]["kind"] == "alert");
  CHECK_NEAR(d["event"]["age_s"].as<float>(), 2.0, 1e-4);
}

static void agesAreRoundedAndNeverNegative() {
  CHECK_NEAR(roundAge(1.04f), 1.0, 1e-6);
  CHECK_NEAR(roundAge(1.06f), 1.1, 1e-6);
  CHECK(roundAge(-3.0f) == 0.0f);
  CHECK(roundAge(NAN) == 0.0f);
}

static void parsesACreatedResponse() {
  ReportResponse r;
  CHECK(parseReportResponse(
      R"({"points":{"stored":5,"route":"6650","status":"ok"},)"
      R"("event":{"status":"created","reason":null,"id_alert":"6651"},"active_route":"6650"})",
      r));
  CHECK(r.pointsStored == 5);
  CHECK(r.pointsStatus == PointsStatus::Ok);
  CHECK(r.eventStatus == EventStatus::Created);
  CHECK(r.eventReason.empty());
  CHECK(r.idAlert == "6651");
  CHECK(r.hasActiveRoute);
  CHECK(r.activeRoute == "6650");
}

static void parsesARejectionAndANullRoute() {
  ReportResponse r;
  CHECK(parseReportResponse(
      R"({"points":{"stored":0,"route":null,"status":"route_finished"},)"
      R"("event":{"status":"rejected","reason":"route_finished","id_alert":null},"active_route":null})",
      r));
  CHECK(r.pointsStatus == PointsStatus::RouteFinished);
  CHECK(r.eventStatus == EventStatus::Rejected);
  CHECK(r.eventReason == "route_finished");
  CHECK(!r.hasActiveRoute);
}

static void parsesDuplicateNoEventAndNoActiveRoute() {
  ReportResponse r;
  CHECK(parseReportResponse(
      R"({"points":{"stored":0,"route":null,"status":"no_active_route"},"event":null,"active_route":null})",
      r));
  CHECK(r.pointsStatus == PointsStatus::NoActiveRoute);
  CHECK(r.eventStatus == EventStatus::Absent);
  CHECK(parseReportResponse(R"({"points":{"stored":1,"status":"ok"},)"
                            R"("event":{"status":"duplicate","id_alert":"a"},"active_route":"r"})",
                            r));
  CHECK(r.eventStatus == EventStatus::Duplicate);
  CHECK(parseReportResponse(R"({"event":{"status":"weird"}})", r));
  CHECK(r.eventStatus == EventStatus::Unknown);
  CHECK(r.pointsStatus == PointsStatus::Unknown);
}

static void unparseableResponseIsRefused() {
  ReportResponse r;
  CHECK(!parseReportResponse("", r));
  CHECK(!parseReportResponse("<html>502</html>", r));
  CHECK(!parseReportResponse("[1]", r));
}

int main() {
  RUN(criticalAlertEventHasTheC2Fields);
  RUN(mediumAlertAndAReasonlessRecord);
  RUN(driverOkEventHasNoSeverity);
  RUN(missingEpisodeFallsBackToTheRecordId);
  RUN(unknownPositionAndSpeedAreNull);
  RUN(scoresWithoutModelKeepTheModelNull);
  RUN(lastKnownPositionIsLabelled);
  RUN(storedEventRoundTripsForADlqReplay);
  RUN(unusableStoredEventsAreRefused);
  RUN(legacyBodiesKeepTheirPosition);
  RUN(reportCarriesPointsOldestFirstWithRelativeAges);
  RUN(reportKeepsOnlyTheNewestTenPoints);
  RUN(emptyReportStillHasAPointsArray);
  RUN(reportWithAnEvent);
  RUN(agesAreRoundedAndNeverNegative);
  RUN(parsesACreatedResponse);
  RUN(parsesARejectionAndANullRoute);
  RUN(parsesDuplicateNoEventAndNoActiveRoute);
  RUN(unparseableResponseIsRefused);
  return report("backend_messages_host_test");
}
