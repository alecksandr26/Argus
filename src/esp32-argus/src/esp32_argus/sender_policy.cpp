#include "sender_policy.h"

#include <cstdio>

namespace {

// millis()-wrap-safe "now is at or past `due`".
bool reached(uint32_t nowMs, uint32_t dueMs) { return (int32_t)(nowMs - dueMs) >= 0; }

uint32_t until(uint32_t nowMs, uint32_t dueMs) { return reached(nowMs, dueMs) ? 0 : dueMs - nowMs; }

}  // namespace

EventTier tierFor(PiEventKind kind) {
  switch (kind) {
    case PiEventKind::AlertCritical: return EventTier::Critical;
    case PiEventKind::AlertMedium: return EventTier::Medium;
    default: return EventTier::Ok;
  }
}

const char* eventTierName(EventTier tier) {
  switch (tier) {
    case EventTier::Critical: return "critical";
    case EventTier::Medium: return "medium";
    case EventTier::Ok: return "ok";
  }
  return "?";
}

float HeldEvent::ageS(uint32_t nowMs) const {
  return baseAgeS + (float)(uint32_t)(nowMs - takenAtMs) / 1000.0f;
}

float HeldEvent::queuedS(uint32_t nowMs) const {
  if (!dlq) return 0.0f;
  return baseQueuedS + (float)(uint32_t)(nowMs - takenAtMs) / 1000.0f;
}

SenderPolicy::SenderPolicy(const SenderTiming& timing) : timing_(timing) {}

bool SenderPolicy::holds(const std::string& id) const {
  if (dlq_.used && dlq_.recordId == id) return true;
  for (const HeldEvent& e : live_) {
    if (e.used && e.recordId == id) return true;
  }
  return false;
}

bool SenderPolicy::livePending() const {
  for (const HeldEvent& e : live_) {
    if (e.used) return true;
  }
  return false;
}

bool SenderPolicy::dlqHeld() const { return dlq_.used; }

TakeResult SenderPolicy::takeLive(const std::string& id, const ReportEvent& event, EventTier tier,
                                  float ageS, uint32_t nowMs) {
  if (holds(id)) return TakeResult::AlreadyHeld;
  if (outcomePendingFor(id)) return TakeResult::AlreadyFinished;
  if (id.empty() || id.size() > IdString::kMaxLength) return TakeResult::Full;  // see recordFitsEvent
  int free = -1;
  for (int i = 0; i < kLiveSlots; i++) {
    if (!live_[i].used) {
      free = i;
      break;
    }
  }
  if (free < 0) return TakeResult::Full;

  HeldEvent& e = live_[free];
  e = HeldEvent();
  e.used = true;
  e.recordId = id;
  e.event = event;
  e.tier = tier;
  e.baseAgeS = ageS > 0.0f ? ageS : 0.0f;
  e.takenAtMs = nowMs;
  e.windowMs = tier == EventTier::Critical ? timing_.liveRetryCriticalMs
               : tier == EventTier::Medium ? timing_.liveRetryMediumMs
                                           : timing_.liveRetryOkMs;

  // Any live event comes before a dead letter: one held but not on its way yet goes back now
  // (not an attempt). One in flight is preempted between AT steps (shouldPreempt).
  if (dlq_.used && inFlight_ != PlanKind::DlqEvent) finish(dlq_, false, "preempted", "");
  return TakeResult::Queued;
}

TakeResult SenderPolicy::takeDlq(const std::string& id, const ReportEvent& event, float ageS,
                                 float queuedS, int priorAttempts, uint32_t nowMs) {
  if (holds(id)) return TakeResult::AlreadyHeld;
  if (outcomePendingFor(id)) return TakeResult::AlreadyFinished;
  if (dlq_.used) return TakeResult::Full;
  if (id.empty() || id.size() > IdString::kMaxLength) return TakeResult::Full;
  dlq_ = HeldEvent();
  dlq_.used = true;
  dlq_.dlq = true;
  dlq_.recordId = id;
  dlq_.event = event;
  dlq_.tier = EventTier::Critical;
  dlq_.baseAgeS = ageS > 0.0f ? ageS : 0.0f;
  dlq_.baseQueuedS = queuedS > 0.0f ? queuedS : 0.0f;
  dlq_.priorAttempts = priorAttempts;
  dlq_.takenAtMs = nowMs;
  return TakeResult::Queued;
}

void SenderPolicy::noteActiveRoute(bool hasActiveRoute) {
  route_ = hasActiveRoute ? RouteKnowledge::Active : RouteKnowledge::None;
}

void SenderPolicy::forgetRoute() { route_ = RouteKnowledge::Unknown; }

void SenderPolicy::expire(uint32_t nowMs) {
  for (int i = 0; i < kLiveSlots; i++) {
    HeldEvent& e = live_[i];
    if (!e.used) continue;
    if (inFlight_ == PlanKind::LiveEvent && inFlightSlot_ == i) continue;
    if ((uint32_t)(nowMs - e.takenAtMs) < e.windowMs) continue;
    if (e.tier == EventTier::Critical) {
      // Dead-letter it with the event as composed (its position at the event): attempt 1 on the
      // Pi. The reason is the last failure; "retry_window" if it never got an attempt.
      std::string reason = e.lastError.empty() ? std::string("retry_window") : e.lastError.str();
      finish(e, false, reason, composeEventJson(e.event, e.ageS(nowMs), EventDelivery::Live, 0));
    } else {
      finish(e, false, "retry_window", "");
    }
  }
}

bool SenderPolicy::eligible(const HeldEvent& e, uint32_t nowMs) const {
  if (e.attempts == 0) return true;
  return reached(nowMs, e.lastAttemptEndMs + timing_.minSendGapMs);
}

int SenderPolicy::bestLive() const {
  int best = -1;
  for (int i = 0; i < kLiveSlots; i++) {
    const HeldEvent& e = live_[i];
    if (!e.used) continue;
    if (best < 0 || e.tier > live_[best].tier) {
      best = i;
    } else if (e.tier == live_[best].tier &&
               (int32_t)(e.takenAtMs - live_[best].takenAtMs) < 0) {
      best = i;  // same tier: the one taken first
    }
  }
  return best;
}

SendPlan SenderPolicy::plan(uint32_t nowMs, bool havePoints) const {
  SendPlan p;
  if (inFlight()) return p;

  int best = bestLive();
  if (best >= 0) {
    const HeldEvent& e = live_[best];
    if (eligible(e, nowMs)) {
      p.kind = PlanKind::LiveEvent;
      p.slot = best;
      p.retry = e.attempts > 0;
    } else {
      p.kind = PlanKind::Wait;
      p.waitMs = until(nowMs, e.lastAttemptEndMs + timing_.minSendGapMs);
    }
    return p;  // nothing else goes while a live event waits
  }

  if (dlq_.used) {
    uint32_t due = lastAttemptFailed_ ? lastReportEndMs_ + timing_.minSendGapMs : nowMs;
    if (reached(nowMs, due)) {
      p.kind = PlanKind::DlqEvent;
      p.retry = true;
    } else {
      p.kind = PlanKind::Wait;
      p.waitMs = until(nowMs, due);
    }
    return p;
  }

  // Points-only. With no point at all it is a liveness report (`points: []`, C2.3 [sync 2]): it
  // keeps device_last_seen_at and the known active route fresh, at the slow cadence.
  if (!everReported_) {
    p.kind = PlanKind::PointsOnly;
    return p;
  }
  uint32_t interval = !havePoints || route_ == RouteKnowledge::None ? timing_.activeRouteRetryMs
                                                                    : timing_.reportIntervalMs;
  uint32_t due = lastReportEndMs_ + interval;
  if (reached(nowMs, due)) {
    p.kind = PlanKind::PointsOnly;
  } else {
    p.kind = PlanKind::Wait;
    p.waitMs = until(nowMs, due);
  }
  return p;
}

void SenderPolicy::beginAttempt(const SendPlan& plan, uint32_t /*nowMs*/) {
  if (plan.kind != PlanKind::PointsOnly && plan.kind != PlanKind::LiveEvent &&
      plan.kind != PlanKind::DlqEvent) {
    return;
  }
  if (plan.kind == PlanKind::LiveEvent && (plan.slot < 0 || plan.slot >= kLiveSlots ||
                                           !live_[plan.slot].used)) {
    return;
  }
  if (plan.kind == PlanKind::DlqEvent && !dlq_.used) return;
  inFlight_ = plan.kind;
  inFlightSlot_ = plan.kind == PlanKind::LiveEvent ? plan.slot : -1;
}

bool SenderPolicy::shouldPreempt(uint32_t nowMs) const {
  switch (inFlight_) {
    case PlanKind::PointsOnly:
    case PlanKind::DlqEvent:
      return livePending();  // any live event comes first
    case PlanKind::LiveEvent: {
      const HeldEvent& current = live_[inFlightSlot_];
      if (current.tier == EventTier::Critical) return false;  // never preempted
      for (int i = 0; i < kLiveSlots; i++) {
        if (i == inFlightSlot_ || !live_[i].used) continue;
        if (live_[i].tier > current.tier && eligible(live_[i], nowMs)) return true;
      }
      return false;
    }
    default:
      return false;
  }
}

void SenderPolicy::endAttempt(AttemptEnd end, const std::string& reason, uint32_t nowMs) {
  PlanKind kind = inFlight_;
  int slot = inFlightSlot_;
  inFlight_ = PlanKind::Idle;
  inFlightSlot_ = -1;
  if (kind == PlanKind::Idle) return;

  if (end != AttemptEnd::Preempted) {
    everReported_ = true;
    lastReportEndMs_ = nowMs;
    lastAttemptFailed_ = end == AttemptEnd::Failed;
  }

  if (kind == PlanKind::LiveEvent) {
    HeldEvent& e = live_[slot];
    switch (end) {
      case AttemptEnd::Delivered: finish(e, true, "", ""); break;
      case AttemptEnd::Rejected: finish(e, false, reason.empty() ? "invalid" : reason, ""); break;
      case AttemptEnd::Failed:
        e.attempts++;
        e.lastError.assignTruncated(reason.empty() ? std::string("transport_error") : reason);
        e.lastAttemptEndMs = nowMs;
        break;
      case AttemptEnd::Preempted: break;  // not counted; it waits for its turn again
    }
  } else if (kind == PlanKind::DlqEvent) {
    switch (end) {
      case AttemptEnd::Delivered: finish(dlq_, true, "", ""); break;
      case AttemptEnd::Rejected:
        finish(dlq_, false, reason.empty() ? "invalid" : reason, "");
        break;
      case AttemptEnd::Failed:  // one try: the Pi counts it
        finish(dlq_, false, reason.empty() ? "transport_error" : reason, "");
        break;
      case AttemptEnd::Preempted: finish(dlq_, false, "preempted", ""); break;
    }
  }
}

void SenderPolicy::finish(HeldEvent& e, bool ack, const std::string& reason,
                          const std::string& body) {
  PiOutcome o;
  o.recordId = e.recordId;
  o.ack = ack;
  o.reason.assignTruncated(reason);
  if (!o.body.assign(body)) bodiesDropped_++;  // too long: "-" (the Pi replays with no position)
  pushOutcome(o);
  e = HeldEvent();
}

void SenderPolicy::pushOutcome(const PiOutcome& outcome) {
  if (outcomeCount_ == kOutcomeSlots) {
    // Ring full (the Pi unreachable for a long time): drop the oldest. Its row's lease runs
    // out on the Pi and it is served again; a resent event is a harmless duplicate.
    outcomeHead_ = (outcomeHead_ + 1) % kOutcomeSlots;
    outcomeCount_--;
    outcomesLost_++;
  }
  outcomes_[(outcomeHead_ + outcomeCount_) % kOutcomeSlots] = outcome;
  outcomeCount_++;
  outcomesPushed_++;
}

const PiOutcome& SenderPolicy::outcome(int i) const {
  return outcomes_[(outcomeHead_ + i) % kOutcomeSlots];
}

void SenderPolicy::dropOutcomes(int n) {
  if (n > outcomeCount_) n = outcomeCount_;
  for (int i = 0; i < n; i++) outcomes_[(outcomeHead_ + i) % kOutcomeSlots] = PiOutcome();
  outcomeHead_ = (outcomeHead_ + n) % kOutcomeSlots;
  outcomeCount_ -= n;
}

void SenderPolicy::dropOutcomesBefore(uint32_t seqEnd) {
  uint32_t firstSeq = outcomesPushed_ - (uint32_t)outcomeCount_;
  int32_t n = (int32_t)(seqEnd - firstSeq);  // wrap-safe
  if (n > 0) dropOutcomes(n);
}

bool SenderPolicy::outcomePendingFor(const std::string& id) const {
  for (int i = 0; i < outcomeCount_; i++) {
    if (outcome(i).recordId == id) return true;
  }
  return false;
}

std::string attemptLogLine(bool dlq, const char* what, const std::string& id, int attempt, int of,
                           const char* transport, uint32_t durationMs, const char* result,
                           const std::string& detail) {
  char head[64];
  if (of > 0) {
    std::snprintf(head, sizeof(head), " attempt %d/%d via %s %.1f s: ", attempt, of, transport,
                  durationMs / 1000.0);
  } else {
    std::snprintf(head, sizeof(head), " attempt %d via %s %.1f s: ", attempt, transport,
                  durationMs / 1000.0);
  }
  std::string line = dlq ? "[dlq] " : "[live] ";
  line += what;
  if (!id.empty()) line += " " + id;
  line += head;
  line += result;
  if (!detail.empty()) line += " (" + detail + ")";
  return line;
}
