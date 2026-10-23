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
  CHECK(s.plan(1000, false).kind == PlanKind::Idle);  // nothing to send
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
  CHECK(deserializeJson(d, o.body) == DeserializationError::Ok);  // the C2 event, for the DLQ
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

static void logLines() {
  CHECK(attemptLogLine(true, "critical", "3f2a9c", 3, 5, "gprs", 7400, "failed", "http_503") ==
        "[dlq] critical 3f2a9c attempt 3/5 via gprs 7.4 s: failed (http_503)");
  CHECK(attemptLogLine(false, "points x5", "", 1, 0, "wifi", 1200, "ok", "") ==
        "[live] points x5 attempt 1 via wifi 1.2 s: ok");
}

int main() {
  RUN(firstPointsOnlyReportGoesAtOnceThenEveryFiveSeconds);
  RUN(noActiveRouteSlowsPointsOnlyReportsButNotEvents);
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
  RUN(survivesMillisWrap);
  RUN(beginAttemptIgnoresPlansWithNothingToSend);
  RUN(logLines);
  return report("sender_policy_host_test");
}
