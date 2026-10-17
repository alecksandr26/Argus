// The grip input feeds fusion directly; a single noisy sample must not flip it (FUS-11).
// update(rawLow) is called at a fixed sample rate; LOW = hand on the wheel = good grip.
// SOURCES: grip_debounce.cpp
#include "check.h"
#include "grip_debounce.h"

static void startsBadUntilSustainedGood() {
  GripDebouncer g(3);
  CHECK(g.state() == GripState::BAD);
  g.update(true);
  g.update(true);
  CHECK(g.state() == GripState::BAD);
  g.update(true);
  CHECK(g.state() == GripState::GOOD);
}

static void singleSpikeIsIgnored() {
  GripDebouncer g(3);
  for (int i = 0; i < 3; i++) g.update(true);
  g.update(false);  // one bounce
  g.update(true);
  g.update(false);
  g.update(true);
  CHECK(g.state() == GripState::GOOD);
}

static void sustainedReleaseFlipsToBad() {
  GripDebouncer g(3);
  for (int i = 0; i < 3; i++) g.update(true);
  for (int i = 0; i < 3; i++) g.update(false);
  CHECK(g.state() == GripState::BAD);
}

int main() {
  RUN(startsBadUntilSustainedGood);
  RUN(singleSpikeIsIgnored);
  RUN(sustainedReleaseFlipsToBad);
  return report("grip_debounce_host_test");
}
