#pragma once

// What the sender task sends next, and what happens to each event after each attempt
// (final-tweaks plan section 1 "ESP32: sending"; contracts C1.8, C2.3, C4). Pure logic with an
// injected millis() clock, so test/host/sender_policy_host_test.cpp runs every rule on the PC:
//
// - One report per POST: the GPS points since the last report plus at most one event. An event
//   goes at once; otherwise a points-only report goes REPORT_INTERVAL_MS after the previous report
//   ended (ACTIVE_ROUTE_RETRY_MS while the backend says the truck has no active route, and for the
//   liveness report with `points: []` sent when there is no position at all).
// - Priority: live CRITICAL > live MEDIUM > live OK > the one DLQ critical (only when no live
//   event is held). Same tier: oldest first. A failed live event is retried no sooner than
//   MIN_SEND_GAP_MS after its attempt ended.
// - Preemption, checked between AT steps of a mobile attempt (never after the request is fired):
//   a higher live event preempts a lower one, a critical is never preempted, any live event
//   preempts a points-only report or a DLQ replay. A preempted attempt is not counted; a
//   preempted DLQ row goes back to the Pi (FAIL preempted).
// - Live retry windows from when the event was taken: CRITICAL 60 s, then the Pi's DLQ (FAIL
//   <last error> <the event JSON>: attempt 1 there); MEDIUM / OK 30 s, then FAIL retry_window.
// - A DLQ replay is one try: delivered -> ACK, rejected -> FAIL <reason>, failed -> FAIL <reason>
//   (the Pi counts it: 5 failed attempts archive the row).
// - Local events (docs/designs/grip-alarm-contracts.md C3): the grip sensor's alert and OK, which
//   this device raised itself. Held in two slots of their own (one alert, one OK) and sent like
//   live Pi events of the same tier (the alert is a live CRITICAL, the OK a live OK: same rules
//   for priority, pre-emption and retry windows), but never reported to the Pi: no PiOutcome, no
//   DLQ. When the window runs out the event is dropped and noted for the log (takeLocalDrop()).
//
// Not thread-safe by itself: the sketch keeps one instance behind a mutex, and the sender task
// never holds that mutex across an HTTP attempt (it copies what it needs, then calls
// shouldPreempt() / endAttempt() under short locks).

#include <cstdint>
#include <string>

#include "backend_messages.h"
#include "config.h"
#include "pi_event_policy.h"

// Live priority. A DLQ replay sits below all of them.
enum class EventTier : uint8_t { Ok = 1, Medium = 2, Critical = 3 };
EventTier tierFor(PiEventKind kind);  // kind must be an event
const char* eventTierName(EventTier tier);

struct SenderTiming {
  uint32_t reportIntervalMs = REPORT_INTERVAL_MS;
  uint32_t activeRouteRetryMs = ACTIVE_ROUTE_RETRY_MS;
  uint32_t minSendGapMs = MIN_SEND_GAP_MS;
  uint32_t liveRetryCriticalMs = LIVE_RETRY_CRITICAL_MS;
  uint32_t liveRetryMediumMs = LIVE_RETRY_MEDIUM_MS;
  uint32_t liveRetryOkMs = LIVE_RETRY_OK_MS;
};

// One event this device holds for the Pi (it TAKEN it; the Pi leases the row for 120 s). A static
// slot: its text is fixed-capacity (bounded_string.h), nothing of it lives on the heap.
struct HeldEvent {
  bool used = false;
  bool dlq = false;
  bool local = false;        // raised here (the grip sensor), not a Pi record: no Pi outcome
  IdString recordId;         // the Pi record id; a local event's client_event_id
  ReportEvent event;
  EventTier tier = EventTier::Ok;
  float baseAgeS = 0.0f;     // the event's age when it was taken (the Pi's age_s)
  float baseQueuedS = 0.0f;  // DLQ: dlq.queued_s when it was taken
  int priorAttempts = 0;     // DLQ: dlq.attempts (failed attempts the Pi counted so far)
  uint32_t takenAtMs = 0;
  uint32_t windowMs = 0;     // live retry window; 0 for a DLQ replay (one try)
  int attempts = 0;          // attempts finished here (a preempted one doesn't count)
  ReasonString lastError;
  uint32_t lastAttemptEndMs = 0;

  float ageS(uint32_t nowMs) const;     // age_s to send now
  float queuedS(uint32_t nowMs) const;  // queued_seconds to send now (DLQ; 0 live)
};

// A finished event, to report to the Pi in the next Bluetooth session. A static slot, like
// HeldEvent.
struct PiOutcome {
  IdString recordId;
  bool ack = true;      // ACK <id>; else FAIL <id> <reason> <body>
  ReasonString reason;  // a token; a longer one is cut
  // The event JSON for the FAIL that first dead-letters a critical, else "". One that doesn't
  // fit goes as "-" (the Pi then replays it with no position; counted in bodiesDropped()).
  EventJsonString body;
};

enum class TakeResult : uint8_t {
  Queued,           // new: TAKEN it
  AlreadyHeld,      // the Pi served a row this device already holds: TAKEN it again, keep one copy
  AlreadyFinished,  // its outcome is waiting for the Pi: don't TAKEN, that outcome finishes it
  Full,             // no slot: leave it on the Pi (served again later); a local event is dropped
};

// A local event given up on, for its log line: its window ran out ("retry_window"), a newer one
// took its slot before it went ("replaced"), or its slot's event was on its way and the new one
// couldn't wait ("slot_busy": the new one is the one dropped).
struct LocalDrop {
  IdString clientEventId;
  EventTier tier = EventTier::Ok;
  ReasonString why;
  int attempts = 0;
  ReasonString lastError;  // the last failed attempt's reason, "" if it never got one
};

enum class RouteKnowledge : uint8_t { Unknown, None, Active };

enum class PlanKind : uint8_t {
  Idle,        // nothing to send (no event, no points)
  Wait,        // something is due in waitMs
  PointsOnly,  // a report with no event: the points since the last one, or none (liveness)
  LiveEvent,   // a report carrying live slot `slot`
  DlqEvent,    // a report carrying the DLQ row
};

struct SendPlan {
  PlanKind kind = PlanKind::Idle;
  int slot = -1;
  uint32_t waitMs = 0;
  bool retry = false;  // tried before, or a DLQ replay: the longer mobile timeout applies
};

enum class AttemptEnd : uint8_t { Delivered, Rejected, Failed, Preempted };

class SenderPolicy {
 public:
  static constexpr int kLiveSlots = SENDER_LIVE_SLOTS;  // live events from the Pi
  // Local events (C3): one alert slot and one OK slot, after the Pi's in the same slot array, so
  // liveSlot(i) and a plan's slot cover both (i < kAllLiveSlots).
  static constexpr int kLocalSlots = 2;
  static constexpr int kLocalAlertSlot = kLiveSlots;
  static constexpr int kLocalOkSlot = kLiveSlots + 1;
  static constexpr int kAllLiveSlots = kLiveSlots + kLocalSlots;
  static constexpr int kLocalDropSlots = 4;
  static constexpr int kOutcomeSlots = SENDER_OUTCOME_SLOTS;

  explicit SenderPolicy(const SenderTiming& timing = SenderTiming());

  // --- The Bluetooth side (a PULL or DLQ session) ---
  // A live event from `PULL`. A DLQ row held but not being sent yet goes back to the Pi at once
  // (FAIL preempted): any live event comes first.
  TakeResult takeLive(const std::string& id, const ReportEvent& event, EventTier tier, float ageS,
                      uint32_t nowMs);
  // The one dead letter from `DLQ 1`.
  TakeResult takeDlq(const std::string& id, const ReportEvent& event, float ageS, float queuedS,
                     int priorAttempts, uint32_t nowMs);
  // --- This device's own events (the grip task, C3) ---
  // A local alert (event.isOk false: a live CRITICAL, 60 s window) or OK (a live OK, 30 s), raised
  // at `nowMs` (its age_s grows from there). A newer one replaces an older one still waiting in
  // its slot (the older one is dropped); if that one is on its way, the new one is dropped
  // instead (Full). Like any live event, it sends a DLQ row held but not on its way back to the
  // Pi. Never reported to the Pi.
  TakeResult takeLocal(const ReportEvent& event, uint32_t nowMs);
  // The oldest local event given up on since the last call, for the log; false when none.
  bool takeLocalDrop(LocalDrop& out);
  uint32_t localDropsLost() const { return localDropsLost_; }  // more drops than log slots

  bool holds(const std::string& id) const;
  bool livePending() const;  // any live event held (the Pi's or a local one), in flight or not
  bool dlqHeld() const;
  // C1.8: ask for `DLQ 1` only with no live event and no DLQ row held (and credentials, which
  // the caller checks).
  bool wantsDlq() const { return !livePending() && !dlqHeld(); }

  // --- The backend's view of the route (C2.3) ---
  void noteActiveRoute(bool hasActiveRoute);  // from a readable 200
  void forgetRoute();                         // after a re-CONFIG: unknown again
  RouteKnowledge route() const { return route_; }

  // --- The sender task ---
  // Gives up on live events whose retry window ran out (not the one in flight): a Pi event's
  // outcome goes to the Pi, a local event is dropped (takeLocalDrop()).
  void expire(uint32_t nowMs);
  // `havePoints`: the track buffer holds at least one point. With none at all a liveness report
  // (points: []) still goes every ACTIVE_ROUTE_RETRY_MS (C2.3 [sync 2]).
  SendPlan plan(uint32_t nowMs, bool havePoints) const;
  const HeldEvent& liveSlot(int i) const { return live_[i]; }
  const HeldEvent& dlqSlot() const { return dlq_; }
  void beginAttempt(const SendPlan& plan, uint32_t nowMs);
  bool inFlight() const { return inFlight_ != PlanKind::Idle; }
  PlanKind inFlightKind() const { return inFlight_; }
  // Between AT steps: should the attempt in flight stop for a higher event?
  bool shouldPreempt(uint32_t nowMs) const;
  // `reason`: the backend's rejection reason, or why the attempt failed.
  void endAttempt(AttemptEnd end, const std::string& reason, uint32_t nowMs);

  // --- Reports for the Pi, oldest first ---
  int outcomeCount() const { return outcomeCount_; }
  const PiOutcome& outcome(int i) const;
  void dropOutcomes(int n);  // the first n reached the Pi (it answered OK)
  // Every outcome gets a sequence number when it is finished. A Bluetooth session notes
  // outcomeSeqEnd() when it copies the outcomes, and drops exactly those once the Pi answers OK
  // with dropOutcomesBefore(): outcomes finished in the meantime stay, even if the ring dropped
  // its oldest entries meanwhile (dropOutcomes(n) would then drop the wrong ones).
  uint32_t outcomeSeqEnd() const { return outcomesPushed_; }
  void dropOutcomesBefore(uint32_t seqEnd);
  bool outcomePendingFor(const std::string& id) const;
  uint32_t outcomesLost() const { return outcomesLost_; }    // overflowed the ring (logged)
  uint32_t bodiesDropped() const { return bodiesDropped_; }  // a FAIL body too long, sent as "-"

 private:
  bool eligible(const HeldEvent& e, uint32_t nowMs) const;
  int bestLive() const;
  void pushOutcome(const PiOutcome& outcome);
  void finish(HeldEvent& e, bool ack, const std::string& reason, const std::string& body);
  void noteLocalDrop(const HeldEvent& e, const char* why);
  void noteLocalDrop(const IdString& id, EventTier tier, int attempts,
                     const ReasonString& lastError, const char* why);
  void releaseDlqForLive();

  SenderTiming timing_;
  HeldEvent live_[kAllLiveSlots];  // the Pi's kLiveSlots, then the two local ones
  HeldEvent dlq_;

  LocalDrop localDrops_[kLocalDropSlots];
  int localDropHead_ = 0;
  int localDropCount_ = 0;
  uint32_t localDropsLost_ = 0;
  RouteKnowledge route_ = RouteKnowledge::Unknown;

  PlanKind inFlight_ = PlanKind::Idle;
  int inFlightSlot_ = -1;
  bool everReported_ = false;
  uint32_t lastReportEndMs_ = 0;
  bool lastAttemptFailed_ = false;

  PiOutcome outcomes_[kOutcomeSlots];
  int outcomeHead_ = 0;
  int outcomeCount_ = 0;
  uint32_t outcomesPushed_ = 0;
  uint32_t outcomesLost_ = 0;
  uint32_t bodiesDropped_ = 0;
};

// One Serial line per attempt, tagged [live] or [dlq]:
// "[dlq] critical 3f2a9c attempt 3/5 via gprs 7.4 s: failed (http_503)".
// `attempt` is 1-based; `of` is 0 for live (no fixed count).
std::string attemptLogLine(bool dlq, const char* what, const std::string& id, int attempt, int of,
                           const char* transport, uint32_t durationMs, const char* result,
                           const std::string& detail);
