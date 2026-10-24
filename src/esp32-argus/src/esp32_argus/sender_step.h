#pragma once

// One report attempt of the sender task, minus the I/O (final-tweaks plan section 1 "ESP32:
// sending"; contracts C1.8, C2.1, C2.3). The sketch's senderTask calls prepareReport() under the
// shared mutex, sends with the mutex released (backend_api.h), then calls finishReport() under
// the mutex again. prepareReport() decides *what* goes (the event and the points, SenderPolicy's
// choice at plan time); composePreparedReport() writes the JSON, and the transport calls it as
// late as it can (right before AT+HTTPDATA on GPRS, right before http.POST on WiFi), so every
// age_s is measured at send time, after registration, bearer setup and the other AT steps
// (C2.1 [sync 3]). Everything here is pure, so test/host/sender_step_host_test.cpp runs the whole
// cycle on the PC: which points a report carries, which go back to the buffer, and how the
// backend's answer (ack_policy.h) moves the event and the known active route.

#include <cstdint>
#include <string>

#include "ack_policy.h"
#include "backend_messages.h"
#include "config.h"
#include "gps_track.h"
#include "sender_policy.h"

struct PreparedReport {
  SendPlan plan;
  bool hasEvent = false;
  bool dlq = false;
  IdString recordId;            // the event's Pi record id ("" for a points-only report)
  EventTier tier = EventTier::Ok;
  int attemptNo = 0;            // 1-based: live, this device's tries; DLQ, the Pi's count + 1
  int pointCount = 0;
  TrackPoint points[REPORT_MAX_POINTS];
  HeldEvent held;               // a copy of the event's slot at plan time (when hasEvent)
};

// Gives up on live events whose retry window ran out, asks the policy what is due and, when
// something is, begins the attempt: takes every point from the track buffer (a liveness report
// takes none) and copies the event. No JSON yet: see composePreparedReport(). False when nothing
// is due yet: `out.plan` is Idle or Wait (with waitMs).
bool prepareReport(SenderPolicy& sender, TrackBuffer& track, uint32_t nowMs, PreparedReport& out);

// The C2.1 body of `rep`, with every age measured at `nowMs`: each point's age_s from its read,
// the event's age_s (and a DLQ replay's queued_seconds) from when it was taken plus the time held
// since. Reads only `rep` (a copy), so the sender task calls it without the shared mutex.
std::string composePreparedReport(const PreparedReport& rep, uint32_t nowMs);

// What finishReport() did, for the attempt's log line.
struct ReportResult {
  const char* result = "";  // delivered | rejected | failed | preempted | ok
  std::string detail;       // the rejection / failure reason, or ""
};

// Ends the attempt `rep` began. `preempted`: it stopped between AT steps for a live event; not
// counted, and its points go back to the buffer (C1.8 [sync 2]). Otherwise `reaction` decides:
// the event is delivered (ACK), rejected (FAIL <reason>) or the attempt failed (retry rules);
// the points are done either way (a failed report's points are dropped: user decision); a
// readable 200 sets the known active route; a 401 forgets it (the caller re-fetches CONFIG).
ReportResult finishReport(SenderPolicy& sender, TrackBuffer& track, const PreparedReport& rep,
                          bool preempted, const ReportReaction& reaction, uint32_t nowMs);

// "[live] critical 3f2a9c1b attempt 2 via gprs 7.4 s: failed (http_503)",
// "[dlq] critical 3f2a9c1b attempt 3/5 via wifi 1.1 s: delivered",
// "[live] points x5 attempt 1 via wifi 0.8 s: ok", "[live] liveness attempt 1 via none 0.0 s:
// failed (no_network)".
std::string reportLogLine(const PreparedReport& rep, const char* transport, uint32_t durationMs,
                          const ReportResult& result);
