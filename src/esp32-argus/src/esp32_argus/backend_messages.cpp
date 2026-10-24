#include "backend_messages.h"

#include <ArduinoJson.h>  // requires ArduinoJson v7+

#include <cmath>

#include "config.h"

namespace {

std::string str(JsonVariantConst v) { return v.is<const char*>() ? v.as<const char*>() : ""; }

void putEvent(JsonObject e, const ReportEvent& ev, float ageS, EventDelivery delivery,
              float queuedSeconds) {
  e["client_event_id"] = ev.clientEventId.c_str();
  e["episode"] = ev.episode.c_str();
  if (ev.isOk) {
    e["kind"] = "ok";
    e["severity"] = nullptr;
    e["alert_type"] = nullptr;
  } else {
    e["kind"] = "alert";
    e["severity"] = ev.severity.c_str();
    e["alert_type"] = ev.alertType.c_str();
  }
  e["age_s"] = roundAge(ageS);
  e["delivery"] = delivery == EventDelivery::Dlq ? "dlq" : "live";
  e["queued_seconds"] = delivery == EventDelivery::Dlq ? roundAge(queuedSeconds) : 0.0f;
  if (ev.position.known) {
    JsonObject p = e["position"].to<JsonObject>();
    p["lat"] = ev.position.lat;
    p["lon"] = ev.position.lon;
    p["last_known"] = ev.position.lastKnown;
    p["location_age_s"] = roundAge(ev.position.locationAgeS);
  } else {
    e["position"] = nullptr;  // "position unknown"
  }
  if (ev.hasSpeed) {
    e["speed"] = std::round(ev.speedKmh * 10.0f) / 10.0f;
  } else {
    e["speed"] = nullptr;
  }
  if (ev.model.empty() && !ev.hasScores) {
    e["ai_metadata"] = nullptr;
  } else {
    JsonObject meta = e["ai_metadata"].to<JsonObject>();
    if (ev.model.empty()) {
      meta["model"] = nullptr;
    } else {
      meta["model"] = ev.model.c_str();
    }
    if (ev.hasScores) {
      JsonObject scores = meta["scores"].to<JsonObject>();
      scores["not_drowsy"] = ev.pNotDrowsy;
      scores["drowsy"] = ev.pDrowsy;
    } else {
      meta["scores"] = nullptr;
    }
  }
}

PointsStatus pointsStatusFrom(const std::string& s) {
  if (s == "ok") return PointsStatus::Ok;
  if (s == "no_active_route") return PointsStatus::NoActiveRoute;
  if (s == "route_finished") return PointsStatus::RouteFinished;
  return PointsStatus::Unknown;
}

EventStatus eventStatusFrom(const std::string& s) {
  if (s == "created") return EventStatus::Created;
  if (s == "duplicate") return EventStatus::Duplicate;
  if (s == "rejected") return EventStatus::Rejected;
  return EventStatus::Unknown;
}

}  // namespace

float roundAge(float seconds) {
  if (!(seconds > 0.0f)) return 0.0f;  // also NaN
  return std::round(seconds * 10.0f) / 10.0f;
}

ReportEvent eventFromRecord(const PiRecord& record, PiEventKind kind,
                            const EventPosition& position, bool hasSpeed, float speedKmh) {
  ReportEvent ev;
  ev.clientEventId = record.id;
  ev.episode = record.episode.empty() ? record.id : record.episode;
  ev.isOk = kind == PiEventKind::DriverOk;
  if (!ev.isOk) {
    ev.severity = kind == PiEventKind::AlertCritical ? "critical" : "medium";
    ev.alertType = record.reason.empty() ? record.className : record.reason;
  }
  ev.position = position;
  ev.hasSpeed = hasSpeed;
  ev.speedKmh = speedKmh;
  ev.model.assignTruncated(record.model);
  ev.hasScores = record.hasProbabilities;
  ev.pNotDrowsy = record.pNotDrowsy;
  ev.pDrowsy = record.pDrowsy;
  return ev;
}

bool recordFitsEvent(const PiRecord& record) {
  const std::string& alertType = record.reason.empty() ? record.className : record.reason;
  return !record.id.empty() && record.id.size() <= IdString::kMaxLength &&
         record.episode.size() <= IdString::kMaxLength &&
         alertType.size() <= AlertTypeString::kMaxLength;
}

bool eventFromStoredJson(const std::string& json, ReportEvent& out) {
  out = ReportEvent();
  JsonDocument doc;
  if (json.empty() || deserializeJson(doc, json) != DeserializationError::Ok ||
      !doc.is<JsonObject>()) {
    return false;
  }
  out.clientEventId = str(doc["client_event_id"]);
  out.episode = str(doc["episode"]);
  std::string kind = str(doc["kind"]);
  if (out.clientEventId.empty() || out.episode.empty()) return false;
  if (kind == "ok") {
    out.isOk = true;
  } else if (kind == "alert") {
    out.severity = str(doc["severity"]);
    out.alertType = str(doc["alert_type"]);
    if (out.severity != "medium" && out.severity != "critical") return false;
    if (out.alertType.empty()) return false;
  } else {
    return false;
  }
  JsonObjectConst p = doc["position"];
  if (!p.isNull() && p["lat"].is<double>() && p["lon"].is<double>()) {
    out.position.known = true;
    out.position.lat = p["lat"].as<double>();
    out.position.lon = p["lon"].as<double>();
    out.position.lastKnown = p["last_known"] | false;
    out.position.locationAgeS = p["location_age_s"] | 0.0f;
  }
  if (doc["speed"].is<float>()) {
    out.hasSpeed = true;
    out.speedKmh = doc["speed"].as<float>();
  }
  JsonObjectConst meta = doc["ai_metadata"];
  if (!meta.isNull()) {
    out.model.assignTruncated(str(meta["model"]));
    JsonObjectConst scores = meta["scores"];
    if (!scores.isNull() && scores["not_drowsy"].is<float>() && scores["drowsy"].is<float>()) {
      out.hasScores = true;
      out.pNotDrowsy = scores["not_drowsy"].as<float>();
      out.pDrowsy = scores["drowsy"].as<float>();
    }
  }
  return true;
}

bool legacyPositionFromBody(const std::string& json, EventPosition& position, bool& hasSpeed,
                            float& speedKmh) {
  position = EventPosition();
  hasSpeed = false;
  speedKmh = 0.0f;
  JsonDocument doc;
  if (json.empty() || deserializeJson(doc, json) != DeserializationError::Ok) return false;
  JsonObjectConst c = doc["coordinates"];
  if (c.isNull() || !c["lat"].is<double>() || !c["lon"].is<double>()) return false;
  position.known = true;
  position.lat = c["lat"].as<double>();
  position.lon = c["lon"].as<double>();
  if (doc["speed_at_event"].is<float>()) {
    hasSpeed = true;
    speedKmh = doc["speed_at_event"].as<float>();
  }
  return true;
}

std::string composeEventJson(const ReportEvent& event, float ageS, EventDelivery delivery,
                             float queuedSeconds) {
  JsonDocument doc;
  putEvent(doc.to<JsonObject>(), event, ageS, delivery, queuedSeconds);
  std::string out;
  serializeJson(doc, out);
  return out;
}

std::string composeReportBody(const TrackPoint* points, int count, uint32_t nowMs,
                              const ReportEvent* event, float eventAgeS, EventDelivery delivery,
                              float queuedSeconds) {
  JsonDocument doc;
  JsonArray arr = doc["points"].to<JsonArray>();
  int first = count > REPORT_MAX_POINTS ? count - REPORT_MAX_POINTS : 0;
  for (int i = first; i < count; i++) {
    const TrackPoint& t = points[i];
    JsonObject p = arr.add<JsonObject>();
    p["age_s"] = roundAge((float)(uint32_t)(nowMs - t.atMs) / 1000.0f);
    p["lat"] = t.lat;
    p["lon"] = t.lon;
    p["speed"] = t.lastKnown ? 0.0f : std::round(t.speedKmh * 10.0f) / 10.0f;
    p["vigilance"] = t.vigilance;
    p["last_known"] = t.lastKnown;
    p["location_age_s"] = t.lastKnown ? roundAge(t.locationAgeS) : 0.0f;
  }
  if (event) putEvent(doc["event"].to<JsonObject>(), *event, eventAgeS, delivery, queuedSeconds);
  std::string out;
  serializeJson(doc, out);
  return out;
}

bool parseReportResponse(const std::string& body, ReportResponse& out) {
  out = ReportResponse();
  JsonDocument doc;
  if (deserializeJson(doc, body) != DeserializationError::Ok || !doc.is<JsonObject>()) {
    return false;
  }
  JsonObjectConst points = doc["points"];
  if (!points.isNull()) {
    out.pointsStored = points["stored"] | 0;
    out.pointsStatus = pointsStatusFrom(str(points["status"]));
  }
  JsonObjectConst event = doc["event"];
  if (!event.isNull()) {
    out.eventStatus = eventStatusFrom(str(event["status"]));
    out.eventReason = str(event["reason"]);
    out.idAlert = str(event["id_alert"]);
  }
  out.activeRoute = str(doc["active_route"]);
  out.hasActiveRoute = !out.activeRoute.empty();
  return true;
}
