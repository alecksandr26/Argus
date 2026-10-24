#pragma once

// The one message this device sends to backend-argus, `POST /api/devices/me/report`
// (docs/designs/final-tweaks-contracts.md C2): the GPS points since the last report plus at most
// one event (an alert, an escalation or a driver OK), and the parsing of its 200 answer. Composed
// apart from sending: every report is logged, and the event object composed for a critical that
// can't be delivered is handed back to the Pi (`FAIL <id> <reason> <event JSON>`), which keeps it
// in its dead-letter queue with the position where it happened. Times are relative (`age_s`):
// only the backend turns them into absolute times. Pure (std::string + ArduinoJson), so
// test/host/backend_messages_host_test.cpp runs it on the PC.

#include <cstdint>
#include <string>

#include "bounded_string.h"
#include "pi_event_policy.h"
#include "pi_record.h"

// This device's own GPS reading at one moment.
struct GpsSnapshot {
  bool hasFix = false;
  double lat = 0.0;
  double lon = 0.0;
  float speedKmh = 0.0f;
};

// One points[] item (C2.1), as read once a second. `atMs` is millis() at the read; the report
// turns it into `age_s` when it is sent.
struct TrackPoint {
  uint32_t atMs = 0;
  double lat = 0.0;
  double lon = 0.0;
  float speedKmh = 0.0f;         // 0 when lastKnown
  const char* vigilance = "unknown";  // low | medium | critical | unknown (a string literal)
  bool lastKnown = false;        // no current fix: the saved last good position
  float locationAgeS = 0.0f;     // seconds between that fix and this read (0 for a live fix)
};

// An event's position (C2.1 `event.position`): where the truck was when the event was first
// composed. known=false is sent as null ("position unknown").
struct EventPosition {
  bool known = false;
  double lat = 0.0;
  double lon = 0.0;
  bool lastKnown = false;
  float locationAgeS = 0.0f;
};

enum class EventDelivery : uint8_t { Live, Dlq };

// The C2.1 `event` object, minus the fields that change with every attempt (age_s, delivery,
// queued_seconds: passed to composeReportBody / composeEventJson).
// Text fields are fixed-capacity (bounded_string.h): a held event is a static sender slot.
struct ReportEvent {
  IdString clientEventId;     // the Pi record id
  IdString episode;           // the Pi payload.episode
  bool isOk = false;          // kind "ok" (driver OK) vs "alert"
  SeverityString severity;    // alerts: "medium" | "critical"
  AlertTypeString alertType;  // alerts: the Pi's reason, e.g. "eyes_closed"
  EventPosition position;
  bool hasSpeed = false;
  float speedKmh = 0.0f;
  ModelString model;          // ai_metadata.model ("" = null; a longer name is cut)
  bool hasScores = false;
  float pNotDrowsy = 0.0f;
  float pDrowsy = 0.0f;
};

// The event for a live Pi record classified as an event (C1.5): alert_type is the Pi's reason
// (its class when it has none), the episode the Pi's (its record id when it has none, so the
// backend doesn't reject it as invalid). `position`/`speed` are this device's at compose time.
// `kind` must not be PiEventKind::None. Check recordFitsEvent() first: an id, episode or reason
// longer than C2.1's 64 chars doesn't fit (and the backend would reject it as invalid).
ReportEvent eventFromRecord(const PiRecord& record, PiEventKind kind,
                            const EventPosition& position, bool hasSpeed, float speedKmh);
bool recordFitsEvent(const PiRecord& record);

// The event's episode and alert type, as eventFromRecord() writes them: also what the buzzer and
// the SMS key and word their decisions by (alarm_policy.h, sms_policy.h).
const std::string& eventEpisode(const PiRecord& record);    // payload.episode, else the id
const std::string& eventAlertType(const PiRecord& record);  // payload.reason, else class_name

// A dead letter's stored event (`dlq.body`, as composeEventJson wrote it): false if it isn't a
// usable one (then compose it from the record with no position, C1.8).
bool eventFromStoredJson(const std::string& json, ReportEvent& out);

// The position (and speed) in a v1 firmware's stored body (the retired POST /api/alerts body:
// `coordinates {lat, lon}`, `speed_at_event`), so a dead letter left over from before the v2
// update still replays with where it happened. False when it has none.
bool legacyPositionFromBody(const std::string& json, EventPosition& position, bool& hasSpeed,
                            float& speedKmh);

// The `event` object alone, compact: the `<body>` of the FAIL that first dead-letters a critical.
std::string composeEventJson(const ReportEvent& event, float ageS, EventDelivery delivery,
                             float queuedSeconds);

// The whole report body. `points` oldest first, at most REPORT_MAX_POINTS of them (only the newest
// that many are kept); each point's age_s is (nowMs - atMs) / 1000. `event` may be null.
std::string composeReportBody(const TrackPoint* points, int count, uint32_t nowMs,
                              const ReportEvent* event, float eventAgeS, EventDelivery delivery,
                              float queuedSeconds);

// Seconds rounded to 0.1, never negative (the wire format for every age).
float roundAge(float seconds);

// The 200 answer (C2.2).
enum class PointsStatus : uint8_t { Ok, NoActiveRoute, RouteFinished, Unknown };
enum class EventStatus : uint8_t { Absent, Created, Duplicate, Rejected, Unknown };

struct ReportResponse {
  int pointsStored = 0;
  PointsStatus pointsStatus = PointsStatus::Unknown;
  EventStatus eventStatus = EventStatus::Absent;  // Absent: "event": null or missing
  std::string eventReason;   // rejected only: route_finished | no_route | too_old | wrong_truck | invalid
  std::string idAlert;
  bool hasActiveRoute = false;  // false = "active_route": null (or missing)
  std::string activeRoute;
};

// False if the body isn't a JSON object.
bool parseReportResponse(const std::string& body, ReportResponse& out);
