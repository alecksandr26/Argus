// The sender task's rules (final-tweaks plan section 1 "ESP32: sending"; contracts C1.8, C2.3,
// C4): report cadence, event priority, preemption between AT steps, live retry windows, the DLQ
// replay's one try, and what is reported back to the Pi.
// SOURCES: sender_policy.cpp backend_messages.cpp pi_event_policy.cpp
// NEEDS: arduinojson
#include <ArduinoJson.h>

#include <string>

#include "check.h"
#include "sender_policy.h"

static ReportEvent ev(const char* id, const char* severity = "critical") {
  ReportEvent e;
  e.clientEventId = id;
  e.episode = std::string("ep-") + id;
  if (std::string(severity) == "ok") {
    e.isOk = true;
  } else {
    e.severity = severity;
    e.alertType = "eyes_closed";
  }
  e.position.known = true;
  e.position.lat = 20.5;
  e.position.lon = -103.25;
  e.hasSpeed = true;
  e.speedKmh = 50;
  return e;
}

static void takeCritical(SenderPolicy& s, const char* id, uint32_t now, float ageS = 0.5f) {
  CHECK(s.takeLive(id, ev(id), EventTier::Critical, ageS, now) == TakeResult::Queued);
}

// One whole attempt for whatever plan() says now.
static SendPlan attempt(SenderPolicy& s, uint32_t now, AttemptEnd end, const char* reason = "",
                        uint32_t durationMs = 0, bool havePoints = true) {
  SendPlan p = s.plan(now, havePoints);
  s.beginAttempt(p, now);
  s.endAttempt(end, reason, now + durationMs);
  return p;
}

// --- Cadence ---

static void firstPointsOnlyReportGoesAtOnceThenEveryFiveSeconds() {
  SenderPolicy s;
  SendPlan p = s.plan(1000, true);
  CHECK(p.kind == PlanKind::PointsOnly);
  s.beginAttempt(p, 1000);
  s.endAttempt(AttemptEnd::Delivered, "", 2500);
  p = s.plan(3000, true);
  CHECK(p.kind == PlanKind::Wait);
  CHECK(p.waitMs == 4500);  // 5 s after the previous report *ended*
  CHECK(s.plan(7500, true).kind == PlanKind::PointsOnly);
}

static void noActiveRouteSlowsPointsOnlyReportsButNotEvents() {
  SenderPolicy s;
  attempt(s, 0, AttemptEnd::Delivered);
  s.noteActiveRoute(false);
  CHECK(s.route() == RouteKnowledge::None);
  CHECK(s.plan(5000, true).kind == PlanKind::Wait);
  CHECK(s.plan(29999, true).kind == PlanKind::Wait);
  CHECK(s.plan(30000, true).kind == PlanKind::PointsOnly);
  takeCritical(s, "c1", 6000);
  CHECK(s.plan(6000, true).kind == PlanKind::LiveEvent);  // events still go at once
}

static void aLivenessReportGoesEveryThirtySecondsWithNoPosition() {
  // C2.3 [sync 2]: no fix this boot and no usable last-known position -> `points: []` every
  // ACTIVE_ROUTE_RETRY_MS, so device_last_seen_at and the active route keep updating.
  SenderPolicy s;
  SendPlan p = s.plan(1000, false);
  CHECK(p.kind == PlanKind::PointsOnly);  // the first one at once
  s.beginAttempt(p, 1000);
  s.endAttempt(AttemptEnd::Delivered, "", 1500);
  s.noteActiveRoute(true);  // the route is known: still the slow cadence with no points
  p = s.plan(6500, false);
  CHECK(p.kind == PlanKind::Wait);
  CHECK(p.waitMs == 25000);
  CHECK(s.plan(31499, false).kind == PlanKind::Wait);
  CHECK(s.plan(31500, false).kind == PlanKind::PointsOnly);
  // Once points exist the usual 5 s cadence is back.
  CHECK(s.plan(6500, true).kind == PlanKind::PointsOnly);
  // Nothing is in flight twice, and an event still goes at once.
  takeCritical(s, "c1", 7000);
  CHECK(s.plan(7000, false).kind == PlanKind::LiveEvent);
}

static void aFailedLivenessReportWaitsThirtySecondsToo() {
  SenderPolicy s;
  attempt(s, 0, AttemptEnd::Failed, "no_network", 100, /*havePoints=*/false);
  CHECK(s.plan(5100, false).kind == PlanKind::Wait);
  CHECK(s.plan(30100, false).kind == PlanKind::PointsOnly);
}

static void unknownRouteKeepsTheFiveSecondCadence() {
  // [sync 1] Before the first 200 (or after a re-CONFIG) the route is unknown, not null.
  SenderPolicy s;
  CHECK(s.route() == RouteKnowledge::Unknown);
  attempt(s, 0, AttemptEnd::Failed, "no_network");
  CHECK(s.plan(5000, true).kind == PlanKind::PointsOnly);
  s.noteActiveRoute(true);
  CHECK(s.route() == RouteKnowledge::Active);
  s.forgetRoute();
  CHECK(s.route() == RouteKnowledge::Unknown);
}

// --- Priority ---

static void criticalBeforeMediumBeforeOkBeforeDlq() {
  SenderPolicy s;
  CHECK(s.takeDlq("d1", ev("d1"), 90, 30, 1, 0) == TakeResult::Queued);
  CHECK(s.plan(0, true).kind == PlanKind::DlqEvent);
  // A live event arriving releases the DLQ row (not on its way yet) back to the Pi.
  CHECK(s.takeLive("o1", ev("o1", "ok"), EventTier::Ok, 0, 10) == TakeResult::Queued);
  CHECK(!s.dlqHeld());
  CHECK(s.outcomeCount() == 1);
  CHECK(!s.outcome(0).ack && s.outcome(0).reason == "preempted" && s.outcome(0).recordId == "d1");
  CHECK(s.takeLive("m1", ev("m1", "medium"), EventTier::Medium, 0, 20) == TakeResult::Queued);
  takeCritical(s, "c1", 30);

  SendPlan p = s.plan(30, true);
  CHECK(p.kind == PlanKind::LiveEvent);
  CHECK(s.liveSlot(p.slot).recordId == "c1");
  attempt(s, 30, AttemptEnd::Delivered);
  p = s.plan(31, true);
  CHECK(s.liveSlot(p.slot).recordId == "m1");
  attempt(s, 31, AttemptEnd::Delivered);
  p = s.plan(32, true);
  CHECK(s.liveSlot(p.slot).recordId == "o1");
  attempt(s, 32, AttemptEnd::Delivered);
  CHECK(!s.livePending());
  CHECK(s.wantsDlq());
}

static void sameTierGoesOldestFirst() {
  SenderPolicy s;
  takeCritical(s, "c-new", 500);
  takeCritical(s, "c-old", 100);  // taken earlier by the clock (order of slots doesn't matter)
  SendPlan p = s.plan(600, true);
  CHECK(s.liveSlot(p.slot).recordId == "c-old");
}

static void noPointsOnlyOrDlqWhileALiveEventWaits() {
  SenderPolicy s;
  takeCritical(s, "c1", 0);
  attempt(s, 0, AttemptEnd::Failed, "http_503", 500);
  SendPlan p = s.plan(600, true);
  CHECK(p.kind == PlanKind::Wait);  // the critical's 1 s gap, not a points-only report
  CHECK(p.waitMs == 900);
  CHECK(!s.wantsDlq());
  CHECK(s.takeDlq("d1", ev("d1"), 1, 1, 1, 600) == TakeResult::Queued);
  CHECK(s.plan(1500, true).kind == PlanKind::LiveEvent);  // live first, the DLQ row waits
}

// --- Retries and windows ---

static void aFailedLiveEventRetriesAfterTheMinimumGap() {
  SenderPolicy s;
  takeCritical(s, "c1", 0);
  SendPlan p = attempt(s, 0, AttemptEnd::Failed, "timeout", 8000);
  CHECK(!p.retry);  // first try: the short mobile timeout
  CHECK(s.liveSlot(p.slot).attempts == 1);
  CHECK(s.liveSlot(p.slot).lastError == "timeout");
  CHECK(s.plan(8999, true).kind == PlanKind::Wait);
  p = s.plan(9000, true);
  CHECK(p.kind == PlanKind::LiveEvent);
  CHECK(p.retry);  // the longer mobile timeout
}

static void aNewEventGoesAtOnceEvenRightAfterAFailure() {
  SenderPolicy s;
  attempt(s, 0, AttemptEnd::Failed, "no_network", 100);  // a points-only report failed
  takeCritical(s, "c1", 150);
  CHECK(s.plan(150, true).kind == PlanKind::LiveEvent);
}

static void criticalDeadLettersAfterSixtySecondsWithItsEvent() {
  SenderPolicy s;
  takeCritical(s, "c1", 1000, 2.0f);
  uint32_t t = 1000;
  for (int guard = 0; guard < 100; guard++) {  // bounded: a regression fails, never hangs
    s.expire(t);
    SendPlan p = s.plan(t, true);
    if (p.kind != PlanKind::LiveEvent) break;
    s.beginAttempt(p, t);
    s.endAttempt(AttemptEnd::Failed, "http_503", t + 8000);
    t += 9000;
  }
  CHECK(!s.livePending());
  CHECK(s.outcomeCount() == 1);
  const PiOutcome& o = s.outcome(0);
  CHECK(o.recordId == "c1");
  CHECK(!o.ack);
  CHECK(o.reason == "http_503");  // the last transport failure
  JsonDocument d;
  CHECK(deserializeJson(d, o.body.c_str()) == DeserializationError::Ok);  // the C2 event (DLQ)
  CHECK(d["client_event_id"] == "c1");
  CHECK(d["severity"] == "critical");
  CHECK(d["delivery"] == "live");
  CHECK_NEAR(d["position"]["lat"].as<double>(), 20.5, 1e-6);
  CHECK(d["age_s"].as<float>() >= 62.0f);  // 2 s old when taken + 60 s here
  CHECK(t - 1000 >= 60000);
}

static void criticalWindowIsSixtySecondsFromWhenItWasTaken() {
  SenderPolicy s;
  takeCritical(s, "c1", 0);
  attempt(s, 0, AttemptEnd::Failed, "no_network");
  s.expire(59999);
  CHECK(s.livePending());
  s.expire(60000);
  CHECK(!s.livePending());
  CHECK(s.outcome(0).reason == "no_network");
}

static void neverAttemptedCriticalStillDeadLetters() {
  SenderPolicy s;
  takeCritical(s, "c1", 0);
  s.expire(60000);
  CHECK(s.outcomeCount() == 1);
  CHECK(s.outcome(0).reason == "retry_window");
  CHECK(!s.outcome(0).body.empty());
}

static void mediumAndOkAreDroppedAfterThirtySeconds() {
  SenderPolicy s;
  CHECK(s.takeLive("m1", ev("m1", "medium"), EventTier::Medium, 0, 0) == TakeResult::Queued);
  CHECK(s.takeLive("o1", ev("o1", "ok"), EventTier::Ok, 0, 5000) == TakeResult::Queued);
  s.expire(29999);
  CHECK(s.outcomeCount() == 0);
  s.expire(30000);
  CHECK(s.outcomeCount() == 1);
  CHECK(s.outcome(0).recordId == "m1");
  CHECK(s.outcome(0).reason == "retry_window");
  CHECK(s.outcome(0).body.empty());  // "-": the Pi archives a non-critical at once
  s.expire(35000);
  CHECK(s.outcomeCount() == 2);
  CHECK(s.outcome(1).recordId == "o1");
  CHECK(s.outcome(1).reason == "retry_window");
}

static void theEventInFlightIsNotExpiredUntilItsAttemptEnds() {
  SenderPolicy s;
  takeCritical(s, "c1", 0);
  SendPlan p = s.plan(55000, true);
  s.beginAttempt(p, 55000);
  s.expire(61000);
  CHECK(s.livePending());  // still in flight
  s.endAttempt(AttemptEnd::Failed, "timeout", 63000);
  s.expire(63000);
  CHECK(!s.livePending());
  CHECK(s.outcome(0).reason == "timeout");
}

static void deliveredAndRejectedLiveEvents() {
  SenderPolicy s;
  takeCritical(s, "c1", 0);
  attempt(s, 0, AttemptEnd::Delivered);
  CHECK(s.outcomeCount() == 1 && s.outcome(0).ack && s.outcome(0).recordId == "c1");
  takeCritical(s, "c2", 10);
  attempt(s, 10, AttemptEnd::Rejected, "route_finished");
  CHECK(!s.outcome(1).ack);
  CHECK(s.outcome(1).reason == "route_finished");
  CHECK(s.outcome(1).body.empty());
  takeCritical(s, "c3", 20);
  attempt(s, 20, AttemptEnd::Rejected, "");
  CHECK(s.outcome(2).reason == "invalid");
}

// --- Preemption (between AT steps) ---

static void higherLiveEventPreemptsALowerOne() {
  SenderPolicy s;
  CHECK(s.takeLive("m1", ev("m1", "medium"), EventTier::Medium, 0, 0) == TakeResult::Queued);
  SendPlan p = s.plan(0, true);
  s.beginAttempt(p, 0);
  CHECK(!s.shouldPreempt(100));
  takeCritical(s, "c1", 200);
  CHECK(s.shouldPreempt(300));
  s.endAttempt(AttemptEnd::Preempted, "", 300);
  CHECK(s.liveSlot(p.slot).attempts == 0);  // not counted
  CHECK(s.outcomeCount() == 0);
  SendPlan next = s.plan(300, true);
  CHECK(s.liveSlot(next.slot).recordId == "c1");  // at once, no gap
}

static void okIsPreemptedByMediumButNotTheOtherWayRound() {
  SenderPolicy s;
  CHECK(s.takeLive("o1", ev("o1", "ok"), EventTier::Ok, 0, 0) == TakeResult::Queued);
  s.beginAttempt(s.plan(0, true), 0);
  CHECK(s.takeLive("m1", ev("m1", "medium"), EventTier::Medium, 0, 10) == TakeResult::Queued);
  CHECK(s.shouldPreempt(20));
  s.endAttempt(AttemptEnd::Preempted, "", 20);

  SenderPolicy t;
  CHECK(t.takeLive("m1", ev("m1", "medium"), EventTier::Medium, 0, 0) == TakeResult::Queued);
  t.beginAttempt(t.plan(0, true), 0);
  CHECK(t.takeLive("o1", ev("o1", "ok"), EventTier::Ok, 0, 10) == TakeResult::Queued);
  CHECK(!t.shouldPreempt(20));
}

static void aCriticalIsNeverPreempted() {
  SenderPolicy s;
  takeCritical(s, "c1", 0);
  s.beginAttempt(s.plan(0, true), 0);
  takeCritical(s, "c2", 10);
  CHECK(!s.shouldPreempt(20));
}

static void aSameTierEventDoesNotPreempt() {
  SenderPolicy s;
  CHECK(s.takeLive("m1", ev("m1", "medium"), EventTier::Medium, 0, 0) == TakeResult::Queued);
  s.beginAttempt(s.plan(0, true), 0);
  CHECK(s.takeLive("m2", ev("m2", "medium"), EventTier::Medium, 0, 10) == TakeResult::Queued);
  CHECK(!s.shouldPreempt(20));
}

static void anyLiveEventPreemptsADlqReplay() {
  SenderPolicy s;
  CHECK(s.takeDlq("d1", ev("d1"), 100, 40, 2, 0) == TakeResult::Queued);
  SendPlan p = s.plan(0, true);
  CHECK(p.kind == PlanKind::DlqEvent);
  CHECK(p.retry);  // a DLQ replay uses the retry timeout
  s.beginAttempt(p, 0);
  CHECK(!s.shouldPreempt(10));
  CHECK(s.takeLive("o1", ev("o1", "ok"), EventTier::Ok, 0, 20) == TakeResult::Queued);
  CHECK(s.dlqHeld());  // in flight: it stays until the AT step boundary
  CHECK(s.shouldPreempt(30));
  s.endAttempt(AttemptEnd::Preempted, "", 30);
  CHECK(!s.dlqHeld());
  CHECK(s.outcomeCount() == 1);
  CHECK(s.outcome(0).recordId == "d1");
  CHECK(s.outcome(0).reason == "preempted");  // back to the DLQ, attempts unchanged on the Pi
}

static void aLiveEventPreemptsAPointsOnlyReport() {
  SenderPolicy s;
  SendPlan p = s.plan(0, true);
  CHECK(p.kind == PlanKind::PointsOnly);
  s.beginAttempt(p, 0);
  CHECK(!s.shouldPreempt(10));
  takeCritical(s, "c1", 20);
  CHECK(s.shouldPreempt(30));
  s.endAttempt(AttemptEnd::Preempted, "", 30);
  CHECK(s.plan(30, true).kind == PlanKind::LiveEvent);
}

static void preemptionWaitsForTheHigherEventsGap() {
  SenderPolicy s;
  takeCritical(s, "c1", 0);
  attempt(s, 0, AttemptEnd::Failed, "http_503", 100);  // c1 waits until 1100
  CHECK(s.takeLive("m1", ev("m1", "medium"), EventTier::Medium, 0, 50) == TakeResult::Queued);
  // plan() never starts the medium while the critical waits for its gap.
  CHECK(s.plan(500, true).kind == PlanKind::Wait);
}

// --- DLQ replay: one try ---

static void dlqReplayIsOneTry() {
  SenderPolicy s;
  CHECK(s.takeDlq("d1", ev("d1"), 100, 40, 2, 0) == TakeResult::Queued);
  attempt(s, 0, AttemptEnd::Failed, "no_network", 50);
  CHECK(!s.dlqHeld());
  CHECK(s.outcome(0).reason == "no_network");  // the Pi counts it (attempt 3 of 5)
  CHECK(s.outcome(0).body.empty());

  CHECK(s.takeDlq("d2", ev("d2"), 100, 40, 0, 100) == TakeResult::Queued);
  // the previous attempt failed: the 1 s gap applies before the next one
  CHECK(s.plan(500, true).kind == PlanKind::Wait);
  attempt(s, 1050, AttemptEnd::Delivered);
  CHECK(s.outcome(1).ack);
  CHECK(s.takeDlq("d3", ev("d3"), 1, 1, 0, 2000) == TakeResult::Queued);
  attempt(s, 2000, AttemptEnd::Rejected, "too_old");
  CHECK(s.outcome(2).reason == "too_old");
}

static void dlqAgeAndQueuedTimeGrowWhileHeld() {
  SenderPolicy s;
  CHECK(s.takeDlq("d1", ev("d1"), 100.0f, 40.0f, 1, 5000) == TakeResult::Queued);
  CHECK_NEAR(s.dlqSlot().ageS(7500), 102.5, 1e-3);
  CHECK_NEAR(s.dlqSlot().queuedS(7500), 42.5, 1e-3);
  takeCritical(s, "c1", 0, 1.0f);
  CHECK_NEAR(s.liveSlot(0).ageS(1500), 2.5, 1e-3);
  CHECK(s.liveSlot(0).queuedS(1500) == 0.0f);
}

static void onlyOneDlqRowAtATime() {
  SenderPolicy s;
  CHECK(s.wantsDlq());
  CHECK(s.takeDlq("d1", ev("d1"), 1, 1, 0, 0) == TakeResult::Queued);
  CHECK(!s.wantsDlq());
  CHECK(s.takeDlq("d2", ev("d2"), 1, 1, 0, 0) == TakeResult::Full);
}

// --- Holding rows (C1.8 dedupe) ---

static void aRowServedAgainIsHeldOnce() {
  SenderPolicy s;
  takeCritical(s, "c1", 0);
  CHECK(s.takeLive("c1", ev("c1"), EventTier::Critical, 0, 5000) == TakeResult::AlreadyHeld);
  // The Pi's lease lapsed and it moved the row to its DLQ: still one copy here.
  CHECK(s.takeDlq("c1", ev("c1"), 0, 0, 0, 6000) == TakeResult::AlreadyHeld);
  CHECK(!s.dlqHeld());
  int used = 0;
  for (int i = 0; i < SenderPolicy::kLiveSlots; i++) used += s.liveSlot(i).used ? 1 : 0;
  CHECK(used == 1);
  CHECK(s.liveSlot(0).takenAtMs == 0);  // keeps its original live window
}

static void aRowWhoseOutcomeWaitsIsNotTakenAgain() {
  SenderPolicy s;
  takeCritical(s, "c1", 0);
  attempt(s, 0, AttemptEnd::Delivered);
  CHECK(s.outcomePendingFor("c1"));
  CHECK(s.takeLive("c1", ev("c1"), EventTier::Critical, 0, 10) == TakeResult::AlreadyFinished);
  s.dropOutcomes(1);  // the Pi confirmed the ACK
  CHECK(!s.outcomePendingFor("c1"));
  CHECK(s.takeLive("c1", ev("c1"), EventTier::Critical, 0, 20) == TakeResult::Queued);
}

static void fullSlotsLeaveTheRowOnThePi() {
  SenderPolicy s;
  for (int i = 0; i < SenderPolicy::kLiveSlots; i++) {
    std::string id = "m" + std::to_string(i);
    CHECK(s.takeLive(id, ev(id.c_str(), "medium"), EventTier::Medium, 0, i) == TakeResult::Queued);
  }
  CHECK(s.takeLive("c1", ev("c1"), EventTier::Critical, 0, 100) == TakeResult::Full);
}

static void outcomesOverflowDropsTheOldest() {
  SenderPolicy s;
  for (int i = 0; i < SenderPolicy::kOutcomeSlots + 2; i++) {
    std::string id = "c" + std::to_string(i);
    takeCritical(s, id.c_str(), i * 10);
    attempt(s, i * 10, AttemptEnd::Delivered);
  }
  CHECK(s.outcomeCount() == SenderPolicy::kOutcomeSlots);
  CHECK(s.outcomesLost() == 2);
  CHECK(s.outcome(0).recordId == "c2");
  s.dropOutcomes(3);
  CHECK(s.outcome(0).recordId == "c5");
  s.dropOutcomes(100);
  CHECK(s.outcomeCount() == 0);
}

static void aSessionDropsOnlyTheOutcomesItCarried() {
  SenderPolicy s;
  takeCritical(s, "c1", 0);
  attempt(s, 0, AttemptEnd::Delivered);
  uint32_t seqEnd = s.outcomeSeqEnd();  // a session copies c1's ACK
  takeCritical(s, "c2", 10);
  attempt(s, 10, AttemptEnd::Delivered);  // finished while that session ran
  s.dropOutcomesBefore(seqEnd);           // the Pi answered OK
  CHECK(s.outcomeCount() == 1);
  CHECK(s.outcome(0).recordId == "c2");
  s.dropOutcomesBefore(seqEnd);  // a second drop for the same session changes nothing
  CHECK(s.outcomeCount() == 1);
}

static void aSessionDropIsRightEvenAfterTheRingOverflowed() {
  SenderPolicy s;
  takeCritical(s, "first", 0);
  attempt(s, 0, AttemptEnd::Delivered);
  uint32_t seqEnd = s.outcomeSeqEnd();  // the session carries "first" only
  for (int i = 0; i < SenderPolicy::kOutcomeSlots; i++) {
    std::string id = "c" + std::to_string(i);
    takeCritical(s, id.c_str(), 10 + i);
    attempt(s, 10 + i, AttemptEnd::Delivered);
  }
  CHECK(s.outcomesLost() == 1);  // "first" fell off the ring meanwhile
  s.dropOutcomesBefore(seqEnd);  // must not drop any of the newer ones
  CHECK(s.outcomeCount() == SenderPolicy::kOutcomeSlots);
  CHECK(s.outcome(0).recordId == "c0");
}

static void slotsAreStaticAndBounded() {
  SenderPolicy s;
  // An id longer than C2.1's 64 chars never gets a slot (pull_session FAILs it invalid first).
  std::string longId(65, 'a');
  CHECK(s.takeLive(longId, ev("x"), EventTier::Critical, 0, 0) == TakeResult::Full);
  CHECK(s.takeDlq(longId, ev("x"), 0, 0, 0, 0) == TakeResult::Full);
  std::string maxId(64, 'b');
  CHECK(s.takeLive(maxId, ev("y"), EventTier::Critical, 0, 0) == TakeResult::Queued);
  CHECK(s.liveSlot(0).recordId == maxId);
  // A failure reason longer than its slot is cut, never lost.
  attempt(s, 0, AttemptEnd::Failed, std::string(60, 'r').c_str());
  CHECK(s.liveSlot(0).lastError.size() == ReasonString::kMaxLength);
  s.expire(60000);
  CHECK(s.outcome(0).reason.size() == ReasonString::kMaxLength);
  CHECK(!s.outcome(0).body.empty());  // a normal event JSON fits its slot
  CHECK(s.bodiesDropped() == 0);
}

static void survivesMillisWrap() {
  SenderPolicy s;
  uint32_t t0 = 0xFFFFF000u;
  takeCritical(s, "c1", t0);
  attempt(s, t0, AttemptEnd::Failed, "timeout", 500);
  CHECK(s.plan(t0 + 1000, true).kind == PlanKind::Wait);
  CHECK(s.plan(t0 + 1500, true).kind == PlanKind::LiveEvent);  // across the wrap
  s.expire(t0 + 59000);
  CHECK(s.livePending());
  s.expire(t0 + 60000);
  CHECK(!s.livePending());
}

static void beginAttemptIgnoresPlansWithNothingToSend() {
  SenderPolicy s;
  SendPlan idle;
  s.beginAttempt(idle, 0);
  CHECK(!s.inFlight());
  SendPlan bogus;
  bogus.kind = PlanKind::LiveEvent;
  bogus.slot = 3;  // empty slot
  s.beginAttempt(bogus, 0);
  CHECK(!s.inFlight());
  s.endAttempt(AttemptEnd::Failed, "x", 0);  // nothing in flight: no effect
  CHECK(s.outcomeCount() == 0);
}

// --- Local events: the grip sensor's own alert and OK (grip-alarm contracts C3) ---

static ReportEvent gripEv(const char* episode, bool ok) {
  ReportEvent e;
  e.clientEventId = std::string(episode) + (ok ? "-ok" : "-a");
  e.episode = episode;
  e.isOk = ok;
  if (!ok) {
    e.severity = "critical";
    e.alertType = "no_grip";
  }
  return e;
}

static int liveUsed(const SenderPolicy& s) {
  int used = 0;
  for (int i = 0; i < SenderPolicy::kAllLiveSlots; i++) used += s.liveSlot(i).used ? 1 : 0;
  return used;
}

static void aGripCriticalIsALiveCriticalOldestFirst() {
  SenderPolicy s;
  CHECK(s.takeLive("m1", ev("m1", "medium"), EventTier::Medium, 0, 0) == TakeResult::Queued);
  takeCritical(s, "c1", 100);
  CHECK(s.takeLocal(gripEv("grip-1a2b3c4d-1", false), 200) == TakeResult::Queued);
  CHECK(s.liveSlot(SenderPolicy::kLocalAlertSlot).tier == EventTier::Critical);
  CHECK(s.liveSlot(SenderPolicy::kLocalAlertSlot).windowMs == LIVE_RETRY_CRITICAL_MS);
  // Same tier as the Pi's critical: the one raised first goes first; both before the medium.
  SendPlan p = s.plan(300, true);
  CHECK(s.liveSlot(p.slot).recordId == "c1");
  attempt(s, 300, AttemptEnd::Delivered);
  p = s.plan(301, true);
  CHECK(p.slot == SenderPolicy::kLocalAlertSlot);
  CHECK(s.liveSlot(p.slot).recordId == "grip-1a2b3c4d-1-a");
  CHECK(!p.retry);  // a first try: the short mobile timeout
  attempt(s, 301, AttemptEnd::Delivered);
  p = s.plan(302, true);
  CHECK(s.liveSlot(p.slot).recordId == "m1");

  // Raised before a Pi critical, it goes first.
  SenderPolicy t;
  CHECK(t.takeLocal(gripEv("grip-1a2b3c4d-2", false), 0) == TakeResult::Queued);
  takeCritical(t, "c2", 50);
  CHECK(t.plan(60, true).slot == SenderPolicy::kLocalAlertSlot);
}

static void aGripOkGoesAfterItsAlertAndBelowAPiMedium() {
  SenderPolicy s;
  CHECK(s.takeLocal(gripEv("grip-1a2b3c4d-1", false), 0) == TakeResult::Queued);
  CHECK(s.takeLocal(gripEv("grip-1a2b3c4d-1", true), 3300) == TakeResult::Queued);
  CHECK(s.liveSlot(SenderPolicy::kLocalOkSlot).tier == EventTier::Ok);
  CHECK(s.liveSlot(SenderPolicy::kLocalOkSlot).windowMs == LIVE_RETRY_OK_MS);
  CHECK(s.takeLive("m1", ev("m1", "medium"), EventTier::Medium, 0, 3400) == TakeResult::Queued);
  CHECK(s.plan(3500, true).slot == SenderPolicy::kLocalAlertSlot);
  attempt(s, 3500, AttemptEnd::Delivered);
  CHECK(s.liveSlot(s.plan(3501, true).slot).recordId == "m1");
  attempt(s, 3501, AttemptEnd::Delivered);
  CHECK(s.plan(3502, true).slot == SenderPolicy::kLocalOkSlot);
}

static void aGripCriticalPreemptsLowerAttemptsButNotACritical() {
  SenderPolicy s;  // a Pi medium on its way
  CHECK(s.takeLive("m1", ev("m1", "medium"), EventTier::Medium, 0, 0) == TakeResult::Queued);
  s.beginAttempt(s.plan(0, true), 0);
  CHECK(s.takeLocal(gripEv("grip-1a2b3c4d-1", false), 10) == TakeResult::Queued);
  CHECK(s.shouldPreempt(20));
  s.endAttempt(AttemptEnd::Preempted, "", 20);
  CHECK(s.plan(20, true).slot == SenderPolicy::kLocalAlertSlot);

  SenderPolicy t;  // a Pi critical on its way: never pre-empted, not even by a grip critical
  takeCritical(t, "c1", 0);
  t.beginAttempt(t.plan(0, true), 0);
  CHECK(t.takeLocal(gripEv("grip-1a2b3c4d-1", false), 10) == TakeResult::Queued);
  CHECK(!t.shouldPreempt(20));

  SenderPolicy u;  // a grip critical on its way isn't pre-empted by a Pi critical either
  CHECK(u.takeLocal(gripEv("grip-1a2b3c4d-1", false), 0) == TakeResult::Queued);
  u.beginAttempt(u.plan(0, true), 0);
  takeCritical(u, "c1", 10);
  CHECK(!u.shouldPreempt(20));

  SenderPolicy v;  // a grip OK on its way is pre-empted by a Pi medium; a points-only report by it
  CHECK(v.takeLocal(gripEv("grip-1a2b3c4d-1", true), 0) == TakeResult::Queued);
  v.beginAttempt(v.plan(0, true), 0);
  CHECK(v.takeLive("m1", ev("m1", "medium"), EventTier::Medium, 0, 10) == TakeResult::Queued);
  CHECK(v.shouldPreempt(20));
  SenderPolicy w;
  w.beginAttempt(w.plan(0, true), 0);
  CHECK(w.inFlightKind() == PlanKind::PointsOnly);
  CHECK(w.takeLocal(gripEv("grip-1a2b3c4d-1", true), 10) == TakeResult::Queued);
  CHECK(w.shouldPreempt(20));
}

static void localEventsNeverProduceOutcomesForThePi() {
  SenderPolicy s;
  CHECK(s.takeLocal(gripEv("grip-1a2b3c4d-1", false), 0) == TakeResult::Queued);
  attempt(s, 0, AttemptEnd::Delivered);
  CHECK(s.takeLocal(gripEv("grip-1a2b3c4d-1", true), 10) == TakeResult::Queued);
  attempt(s, 10, AttemptEnd::Rejected, "route_finished");
  CHECK(s.takeLocal(gripEv("grip-1a2b3c4d-2", false), 20) == TakeResult::Queued);
  attempt(s, 20, AttemptEnd::Failed, "timeout", 8000);
  s.expire(60020);  // the critical's window: dropped, not dead-lettered
  CHECK(!s.livePending());
  CHECK(s.outcomeCount() == 0);  // no ACK, no FAIL: there is no Pi row behind them
  CHECK(s.outcomeSeqEnd() == 0);
  CHECK(s.bodiesDropped() == 0);
}

static void aLocalEventIsDroppedAndLoggedWhenItsWindowRunsOut() {
  SenderPolicy s;
  CHECK(s.takeLocal(gripEv("grip-1a2b3c4d-1", false), 1000) == TakeResult::Queued);
  CHECK(s.takeLocal(gripEv("grip-1a2b3c4d-1", true), 5000) == TakeResult::Queued);
  attempt(s, 1000, AttemptEnd::Failed, "no_network", 100);
  LocalDrop d;
  CHECK(!s.takeLocalDrop(d));
  s.expire(34999);  // the OK's 30 s from 5000
  CHECK(!s.takeLocalDrop(d));
  s.expire(35000);
  CHECK(s.takeLocalDrop(d));
  CHECK(d.clientEventId == "grip-1a2b3c4d-1-ok" && d.tier == EventTier::Ok);
  CHECK(d.why == "retry_window" && d.attempts == 0 && d.lastError.empty());
  CHECK(!s.takeLocalDrop(d));
  s.expire(60999);  // the alert's 60 s from 1000
  CHECK(s.livePending());
  s.expire(61000);
  CHECK(!s.livePending());
  CHECK(s.takeLocalDrop(d));
  CHECK(d.clientEventId == "grip-1a2b3c4d-1-a" && d.tier == EventTier::Critical);
  CHECK(d.why == "retry_window" && d.attempts == 1 && d.lastError == "no_network");
  CHECK(s.outcomeCount() == 0);
}

static void aLocalEventInFlightIsNotExpiredUntilItsAttemptEnds() {
  SenderPolicy s;
  CHECK(s.takeLocal(gripEv("grip-1a2b3c4d-1", false), 0) == TakeResult::Queued);
  s.beginAttempt(s.plan(59000, true), 59000);
  s.expire(61000);
  CHECK(s.livePending());
  s.endAttempt(AttemptEnd::Failed, "timeout", 62000);
  s.expire(62000);
  CHECK(!s.livePending());
  LocalDrop d;
  CHECK(s.takeLocalDrop(d) && d.lastError == "timeout");
}

static void aNewerGripEpisodeReplacesAnOlderUnsentOne() {
  SenderPolicy s;
  CHECK(s.takeLocal(gripEv("grip-1a2b3c4d-1", false), 0) == TakeResult::Queued);
  CHECK(s.takeLocal(gripEv("grip-1a2b3c4d-2", false), 4000) == TakeResult::Queued);
  CHECK(s.liveSlot(SenderPolicy::kLocalAlertSlot).recordId == "grip-1a2b3c4d-2-a");
  CHECK(s.liveSlot(SenderPolicy::kLocalAlertSlot).takenAtMs == 4000);
  LocalDrop d;
  CHECK(s.takeLocalDrop(d) && d.clientEventId == "grip-1a2b3c4d-1-a" && d.why == "replaced");
  CHECK(liveUsed(s) == 1);
  // The same event again is held once.
  CHECK(s.takeLocal(gripEv("grip-1a2b3c4d-2", false), 4100) == TakeResult::AlreadyHeld);
  CHECK(s.liveSlot(SenderPolicy::kLocalAlertSlot).takenAtMs == 4000);

  // On its way, the older one can't be dropped: the new one is.
  s.beginAttempt(s.plan(4200, true), 4200);
  CHECK(s.takeLocal(gripEv("grip-1a2b3c4d-3", false), 4300) == TakeResult::Full);
  CHECK(s.takeLocalDrop(d) && d.clientEventId == "grip-1a2b3c4d-3-a" && d.why == "slot_busy");
  CHECK(s.liveSlot(SenderPolicy::kLocalAlertSlot).recordId == "grip-1a2b3c4d-2-a");
}

static void localSlotsAreTheirOwn() {
  SenderPolicy s;
  for (int i = 0; i < SenderPolicy::kLiveSlots; i++) {
    std::string id = "m" + std::to_string(i);
    CHECK(s.takeLive(id, ev(id.c_str(), "medium"), EventTier::Medium, 0, i) == TakeResult::Queued);
  }
  CHECK(s.takeLive("c1", ev("c1"), EventTier::Critical, 0, 100) == TakeResult::Full);
  CHECK(s.takeLocal(gripEv("grip-1a2b3c4d-1", false), 100) == TakeResult::Queued);  // still room
  CHECK(s.takeLocal(gripEv("grip-1a2b3c4d-1", true), 100) == TakeResult::Queued);
  CHECK(liveUsed(s) == SenderPolicy::kAllLiveSlots);
  CHECK(s.holds("grip-1a2b3c4d-1-a"));
  CHECK(s.plan(100, true).slot == SenderPolicy::kLocalAlertSlot);  // the critical, before mediums
}

static void aLocalEventComesBeforeADeadLetter() {
  SenderPolicy s;
  CHECK(s.takeDlq("d1", ev("d1"), 90, 30, 1, 0) == TakeResult::Queued);
  CHECK(s.takeLocal(gripEv("grip-1a2b3c4d-1", false), 10) == TakeResult::Queued);
  CHECK(!s.dlqHeld());  // not on its way yet: back to the Pi at once (not an attempt)
  CHECK(s.outcomeCount() == 1 && s.outcome(0).recordId == "d1");
  CHECK(s.outcome(0).reason == "preempted");
  CHECK(!s.wantsDlq());  // no DLQ 1 while a local event is held

  SenderPolicy t;  // a replay on its way is pre-empted between AT steps
  CHECK(t.takeDlq("d1", ev("d1"), 90, 30, 1, 0) == TakeResult::Queued);
  t.beginAttempt(t.plan(0, true), 0);
  CHECK(t.takeLocal(gripEv("grip-1a2b3c4d-1", true), 10) == TakeResult::Queued);
  CHECK(t.dlqHeld());
  CHECK(t.shouldPreempt(20));
}

static void localDropsKeepTheNewestWhenNobodyReadsThem() {
  SenderPolicy s;
  for (int i = 1; i <= SenderPolicy::kLocalDropSlots + 2; i++) {
    std::string episode = "grip-1a2b3c4d-" + std::to_string(i);
    CHECK(s.takeLocal(gripEv(episode.c_str(), false), i * 4000) == TakeResult::Queued);
  }
  CHECK(s.localDropsLost() == 1);  // 5 replaced, 4 slots
  LocalDrop d;
  CHECK(s.takeLocalDrop(d) && d.clientEventId == "grip-1a2b3c4d-2-a");
}

static void logLines() {
  CHECK(attemptLogLine(true, "critical", "3f2a9c", 3, 5, "gprs", 7400, "failed", "http_503") ==
        "[dlq] critical 3f2a9c attempt 3/5 via gprs 7.4 s: failed (http_503)");
  CHECK(attemptLogLine(false, "points x5", "", 1, 0, "wifi", 1200, "ok", "") ==
        "[live] points x5 attempt 1 via wifi 1.2 s: ok");
}

int main() {
  RUN(firstPointsOnlyReportGoesAtOnceThenEveryFiveSeconds);
  RUN(noActiveRouteSlowsPointsOnlyReportsButNotEvents);
  RUN(aLivenessReportGoesEveryThirtySecondsWithNoPosition);
  RUN(aFailedLivenessReportWaitsThirtySecondsToo);
  RUN(unknownRouteKeepsTheFiveSecondCadence);
  RUN(criticalBeforeMediumBeforeOkBeforeDlq);
  RUN(sameTierGoesOldestFirst);
  RUN(noPointsOnlyOrDlqWhileALiveEventWaits);
  RUN(aFailedLiveEventRetriesAfterTheMinimumGap);
  RUN(aNewEventGoesAtOnceEvenRightAfterAFailure);
  RUN(criticalDeadLettersAfterSixtySecondsWithItsEvent);
  RUN(criticalWindowIsSixtySecondsFromWhenItWasTaken);
  RUN(neverAttemptedCriticalStillDeadLetters);
  RUN(mediumAndOkAreDroppedAfterThirtySeconds);
  RUN(theEventInFlightIsNotExpiredUntilItsAttemptEnds);
  RUN(deliveredAndRejectedLiveEvents);
  RUN(higherLiveEventPreemptsALowerOne);
  RUN(okIsPreemptedByMediumButNotTheOtherWayRound);
  RUN(aCriticalIsNeverPreempted);
  RUN(aSameTierEventDoesNotPreempt);
  RUN(anyLiveEventPreemptsADlqReplay);
  RUN(aLiveEventPreemptsAPointsOnlyReport);
  RUN(preemptionWaitsForTheHigherEventsGap);
  RUN(dlqReplayIsOneTry);
  RUN(dlqAgeAndQueuedTimeGrowWhileHeld);
  RUN(onlyOneDlqRowAtATime);
  RUN(aRowServedAgainIsHeldOnce);
  RUN(aRowWhoseOutcomeWaitsIsNotTakenAgain);
  RUN(fullSlotsLeaveTheRowOnThePi);
  RUN(outcomesOverflowDropsTheOldest);
  RUN(aSessionDropsOnlyTheOutcomesItCarried);
  RUN(aSessionDropIsRightEvenAfterTheRingOverflowed);
  RUN(slotsAreStaticAndBounded);
  RUN(survivesMillisWrap);
  RUN(beginAttemptIgnoresPlansWithNothingToSend);
  RUN(aGripCriticalIsALiveCriticalOldestFirst);
  RUN(aGripOkGoesAfterItsAlertAndBelowAPiMedium);
  RUN(aGripCriticalPreemptsLowerAttemptsButNotACritical);
  RUN(localEventsNeverProduceOutcomesForThePi);
  RUN(aLocalEventIsDroppedAndLoggedWhenItsWindowRunsOut);
  RUN(aLocalEventInFlightIsNotExpiredUntilItsAttemptEnds);
  RUN(aNewerGripEpisodeReplacesAnOlderUnsentOne);
  RUN(localSlotsAreTheirOwn);
  RUN(aLocalEventComesBeforeADeadLetter);
  RUN(localDropsKeepTheNewestWhenNobodyReadsThem);
  RUN(logLines);
  return report("sender_policy_host_test");
}
