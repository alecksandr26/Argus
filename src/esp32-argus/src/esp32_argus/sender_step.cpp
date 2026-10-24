#include "sender_step.h"

bool prepareReport(SenderPolicy& sender, TrackBuffer& track, uint32_t nowMs, PreparedReport& out) {
  out.hasEvent = false;
  out.dlq = false;
  out.recordId.clear();
  out.tier = EventTier::Ok;
  out.attemptNo = 1;
  out.pointCount = 0;
  out.held = HeldEvent();

  sender.expire(nowMs);
  out.plan = sender.plan(nowMs, track.size() > 0);
  const HeldEvent* held = nullptr;
  switch (out.plan.kind) {
    case PlanKind::LiveEvent: held = &sender.liveSlot(out.plan.slot); break;
    case PlanKind::DlqEvent: held = &sender.dlqSlot(); break;
    case PlanKind::PointsOnly: break;
    default: return false;  // Idle / Wait
  }

  sender.beginAttempt(out.plan, nowMs);
  if (!sender.inFlight()) return false;  // a plan with nothing behind it (never expected)
  out.pointCount = track.take(out.points);

  if (held) {
    out.hasEvent = true;
    out.dlq = held->dlq;
    out.recordId = held->recordId;
    out.tier = held->tier;
    out.attemptNo = held->dlq ? held->priorAttempts + 1 : held->attempts + 1;
    out.held = *held;
  }
  return true;
}

std::string composePreparedReport(const PreparedReport& rep, uint32_t nowMs) {
  if (!rep.hasEvent) {
    return composeReportBody(rep.points, rep.pointCount, nowMs, nullptr, 0.0f,
                             EventDelivery::Live, 0.0f);
  }
  const HeldEvent& e = rep.held;
  return composeReportBody(rep.points, rep.pointCount, nowMs, &e.event, e.ageS(nowMs),
                           e.dlq ? EventDelivery::Dlq : EventDelivery::Live, e.queuedS(nowMs));
}

ReportResult finishReport(SenderPolicy& sender, TrackBuffer& track, const PreparedReport& rep,
                          bool preempted, const ReportReaction& reaction, uint32_t nowMs) {
  ReportResult r;
  if (preempted) {
    sender.endAttempt(AttemptEnd::Preempted, "", nowMs);
    track.restore(rep.points, rep.pointCount);  // not sent: they ride the next report
    r.result = "preempted";
    return r;
  }

  if (reaction.routeKnown) sender.noteActiveRoute(!reaction.activeRoute.empty());
  if (reaction.recredential) sender.forgetRoute();  // unknown until the next readable 200

  if (!rep.hasEvent) {
    bool ok = reaction.routeKnown;
    sender.endAttempt(ok ? AttemptEnd::Delivered : AttemptEnd::Failed, reaction.reason, nowMs);
    r.result = ok ? "ok" : "failed";
    if (!ok) r.detail = reaction.reason;
    return r;
  }

  switch (reaction.event) {
    case EventOutcome::Delivered:
      sender.endAttempt(AttemptEnd::Delivered, "", nowMs);
      r.result = "delivered";
      break;
    case EventOutcome::Rejected:
      sender.endAttempt(AttemptEnd::Rejected, reaction.reason, nowMs);
      r.result = "rejected";
      r.detail = reaction.reason;
      break;
    case EventOutcome::AttemptFailed:
      sender.endAttempt(AttemptEnd::Failed, reaction.reason, nowMs);
      r.result = "failed";
      r.detail = reaction.reason;
      break;
    case EventOutcome::NoEvent:  // reactToReport was told there was no event: treat as unread
      sender.endAttempt(AttemptEnd::Failed, "bad_response", nowMs);
      r.result = "failed";
      r.detail = "bad_response";
      break;
  }
  return r;
}

std::string reportLogLine(const PreparedReport& rep, const char* transport, uint32_t durationMs,
                          const ReportResult& result) {
  std::string what;
  if (rep.hasEvent) {
    what = eventTierName(rep.tier);
  } else if (rep.pointCount > 0) {
    what = "points x" + std::to_string(rep.pointCount);
  } else {
    what = "liveness";
  }
  std::string id = rep.recordId.str().substr(0, 8);
  int of = rep.dlq ? DLQ_MAX_ATTEMPTS : 0;
  return attemptLogLine(rep.dlq, what.c_str(), id, rep.attemptNo, of, transport, durationMs,
                        result.result, result.detail);
}
