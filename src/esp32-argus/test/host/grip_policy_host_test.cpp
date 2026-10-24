// The steering-wheel grip rule (grip-alarm contracts C2) and its events' ids and fields (C3): 3 s
// without pressure starts an episode, 300 ms of pressure ends it, the optional moving gate, the
// episode counter.
// SOURCES: grip_policy.cpp
#include <string>

#include "check.h"
#include "grip_policy.h"

// Samples every 100 ms from `from` (inclusive) to `to` (exclusive); returns the first change.
static GripChange run(GripPolicy& g, bool pressure, uint32_t from, uint32_t to,
                      uint32_t* changedAt = nullptr, bool moving = true) {
  for (uint32_t t = from; t < to; t += GRIP_SAMPLE_INTERVAL_MS) {
    GripChange c = g.onSample(pressure, moving, t);
    if (c != GripChange::None) {
      if (changedAt) *changedAt = t;
      return c;
    }
  }
  return GripChange::None;
}

static void pressureIsAnySensorAboveTheThreshold() {
  int none[4] = {0, 120, 500, 499};
  int one[4] = {0, 0, 0, 501};
  int all[4] = {4095, 900, 800, 2000};
  CHECK(!gripHasPressure(none, 4));  // 500 itself isn't above 500
  CHECK(gripHasPressure(one, 4));
  CHECK(gripHasPressure(all, 4));
  CHECK(!gripHasPressure(one, 3));  // only the sensors counted
  CHECK(gripHasPressure(none, 4, 100));
}

static void threeSecondsWithoutPressureStartAnEpisode() {
  GripPolicy g;
  CHECK(run(g, true, 0, 1000) == GripChange::None);
  uint32_t at = 0;
  CHECK(run(g, false, 1000, 3900, &at) == GripChange::None);  // 2.9 s
  CHECK(!g.alarm());
  CHECK(g.noPressureMs(3900) == 2900);
  CHECK(g.onSample(false, true, 4000) == GripChange::EpisodeStarted);  // 3.0 s
  CHECK(g.alarm());
  CHECK(g.episode() == 1);
  CHECK(run(g, false, 4100, 20000) == GripChange::None);  // one start per episode
  CHECK(g.alarm());
}

static void pressureInBetweenRestartsTheTimer() {
  GripPolicy g;
  CHECK(run(g, false, 0, 2500) == GripChange::None);
  CHECK(g.onSample(true, true, 2500) == GripChange::None);  // one sample of pressure
  CHECK(g.noPressureMs(2500) == 0);
  uint32_t at = 0;
  CHECK(run(g, false, 2600, 10000, &at) == GripChange::EpisodeStarted);
  CHECK(at == 5600);  // 3 s from the first no-pressure sample after it
}

static void threeHundredMillisecondsOfPressureEndTheEpisode() {
  GripPolicy g;
  CHECK(run(g, false, 0, 10000) == GripChange::EpisodeStarted);
  CHECK(g.onSample(true, true, 5000) == GripChange::None);
  CHECK(g.onSample(true, true, 5100) == GripChange::None);
  CHECK(g.onSample(true, true, 5200) == GripChange::None);
  CHECK(g.alarm());  // 200 ms: not yet
  CHECK(g.onSample(true, true, 5300) == GripChange::EpisodeEnded);
  CHECK(!g.alarm());
  CHECK(g.episode() == 1);  // the ended episode's number stays (its OK's id)
}

static void theRecoveryMustBeContinuous() {
  GripPolicy g;
  CHECK(run(g, false, 0, 10000) == GripChange::EpisodeStarted);
  CHECK(g.onSample(true, true, 5000) == GripChange::None);
  CHECK(g.onSample(true, true, 5200) == GripChange::None);
  CHECK(g.onSample(false, true, 5250) == GripChange::None);  // let go again
  CHECK(g.onSample(true, true, 5300) == GripChange::None);   // the 300 ms start over
  CHECK(g.onSample(true, true, 5500) == GripChange::None);
  CHECK(g.alarm());
  CHECK(g.onSample(true, true, 5600) == GripChange::EpisodeEnded);
}

static void theNextEpisodeNeedsAFullThreeSecondsAgain() {
  GripPolicy g;
  CHECK(run(g, false, 0, 10000) == GripChange::EpisodeStarted);
  CHECK(run(g, true, 4000, 5000) == GripChange::EpisodeEnded);
  uint32_t at = 0;
  CHECK(run(g, false, 5000, 20000, &at) == GripChange::EpisodeStarted);
  CHECK(at == 8000);
  CHECK(g.episode() == 2);  // the counter goes on
}

static void theMovingGateHoldsTheTimerWhileStopped() {
  GripTiming timing;
  timing.requireMoving = true;
  GripPolicy g(timing);
  // Parked, hands off: no alarm, however long.
  CHECK(run(g, false, 0, 60000, nullptr, /*moving=*/false) == GripChange::None);
  CHECK(g.noPressureMs(60000) == 0);
  // Moving, hands off: 3 s from when it started moving.
  uint32_t at = 0;
  CHECK(run(g, false, 60000, 70000, &at, true) == GripChange::EpisodeStarted);
  CHECK(at == 63000);
  // Stopping doesn't end an episode: only pressure does.
  CHECK(run(g, false, 63100, 70000, nullptr, false) == GripChange::None);
  CHECK(g.alarm());
  CHECK(run(g, true, 70000, 71000, nullptr, false) == GripChange::EpisodeEnded);
  // A stop in the middle of the 3 s restarts them.
  CHECK(run(g, false, 71000, 73000, nullptr, true) == GripChange::None);
  CHECK(g.onSample(false, false, 73000) == GripChange::None);
  CHECK(run(g, false, 73100, 80000, &at, true) == GripChange::EpisodeStarted);
  CHECK(at == 76100);

  // Without the gate (the default), a parked truck alarms too.
  GripPolicy plain;
  CHECK(run(plain, false, 0, 5000, &at, false) == GripChange::EpisodeStarted);
  CHECK(at == 3000);
}

static void movingNeedsAFixAndFiveKmh() {
  CHECK(gripIsMoving(true, 5.0f));
  CHECK(gripIsMoving(true, 80.0f));
  CHECK(!gripIsMoving(true, 4.9f));
  CHECK(!gripIsMoving(false, 80.0f));  // no fix: not moving
}

static void survivesMillisWrap() {
  GripPolicy g;
  const uint32_t t0 = 0xFFFFFFFFu - 1000u;  // millis() wraps 1 s into the 3 s
  uint32_t t = t0;
  int samples = 0;
  while (g.onSample(false, true, t) == GripChange::None && samples < 100) {
    t += GRIP_SAMPLE_INTERVAL_MS;
    samples++;
  }
  CHECK(g.alarm());
  CHECK(samples == 30);  // exactly 3 s later, across the wrap
  CHECK(t == t0 + 3000u);
  // The recovery across a wrap too.
  GripPolicy r;
  CHECK(run(r, false, 0, 4000) == GripChange::EpisodeStarted);
  CHECK(r.onSample(true, true, 0xFFFFFF00u) == GripChange::None);
  CHECK(r.onSample(true, true, 0x0000002Cu) == GripChange::EpisodeEnded);  // 300 ms later
}

static void idsFollowTheContract() {
  CHECK(gripEpisodeId(0x1a2b3c4du, 1) == "grip-1a2b3c4d-1");
  CHECK(gripEpisodeId(0x0000000fu, 42) == "grip-0000000f-42");  // always 8 hex digits
  CHECK(gripEpisodeId(0xffffffffu, 4294967295u) == "grip-ffffffff-4294967295");
}

static void theAlertAndTheOkEvents() {
  EventPosition here;
  here.known = true;
  here.lat = 20.6597;
  here.lon = -103.3496;
  ReportEvent a = gripAlertEvent(0x1a2b3c4du, 3, here, true, 61.5f);
  CHECK(a.clientEventId == "grip-1a2b3c4d-3-a");
  CHECK(a.episode == "grip-1a2b3c4d-3");
  CHECK(!a.isOk);
  CHECK(a.severity == "critical");
  CHECK(a.alertType == "no_grip");
  CHECK(a.position.known && a.position.lat == 20.6597);
  CHECK(a.hasSpeed && a.speedKmh == 61.5f);
  CHECK(a.model.empty() && !a.hasScores);  // ai_metadata: null

  ReportEvent ok = gripOkEvent(0x1a2b3c4du, 3, EventPosition(), false, 0.0f);
  CHECK(ok.clientEventId == "grip-1a2b3c4d-3-ok");
  CHECK(ok.episode == "grip-1a2b3c4d-3");
  CHECK(ok.isOk);
  CHECK(ok.severity.empty() && ok.alertType.empty());
  CHECK(!ok.position.known);  // no position: "position unknown"
  CHECK(!ok.hasSpeed);
}

int main() {
  RUN(pressureIsAnySensorAboveTheThreshold);
  RUN(threeSecondsWithoutPressureStartAnEpisode);
  RUN(pressureInBetweenRestartsTheTimer);
  RUN(threeHundredMillisecondsOfPressureEndTheEpisode);
  RUN(theRecoveryMustBeContinuous);
  RUN(theNextEpisodeNeedsAFullThreeSecondsAgain);
  RUN(theMovingGateHoldsTheTimerWhileStopped);
  RUN(movingNeedsAFixAndFiveKmh);
  RUN(survivesMillisWrap);
  RUN(idsFollowTheContract);
  RUN(theAlertAndTheOkEvents);
  return report("grip_policy_host_test");
}
