// Panic must never be missed or lost: a press during a multi-second Bluetooth/HTTP call is
// latched by the interrupt (PAN-01), and the event is queued (persisted to NVS) and retried until
// the backend confirms it (PAN-03).
// SOURCES: panic_latch.cpp
#include <string>

#include "check.h"
#include "panic_latch.h"

static void pressWhileBusyIsLatchedOnce() {
  PanicLatch latch(50);
  latch.onFallingEdge(1000);  // ISR fires while the main loop is blocked
  CHECK(latch.takePending());
  CHECK(!latch.takePending());
}

static void bounceCountsOnce() {
  PanicLatch latch(50);
  latch.onFallingEdge(1000);
  latch.onFallingEdge(1010);
  latch.onFallingEdge(1040);
  CHECK(latch.takePending());
  CHECK(!latch.takePending());
  latch.onFallingEdge(1200);  // a real second press
  CHECK(latch.takePending());
}

static void millisWrapStillDebounces() {
  PanicLatch latch(50);
  latch.onFallingEdge(0xFFFFFFF0u);
  latch.takePending();
  latch.onFallingEdge(0x00000010u);  // 32 ms later across the wrap: a bounce
  CHECK(!latch.takePending());
}

static void queuedEventStaysUntilPosted() {
  PanicQueue q;
  q.push({1000, true, 20.65, -103.37});
  CHECK(q.size() == 1);
  CHECK(q.front().atMs == 1000);
  q.markFrontPosted();
  CHECK(q.size() == 0);
}

static void queueSurvivesReboot() {
  PanicQueue q;
  q.push({1000, true, 20.65, -103.37});
  q.push({2000, false, 0, 0});
  std::string blob = q.serialize();  // what goes into NVS

  PanicQueue restored;
  CHECK(restored.deserialize(blob));
  CHECK(restored.size() == 2);
  CHECK(restored.front().atMs == 1000);
  CHECK(!restored.deserialize("garbage"));
}

static void queueIsBounded() {
  PanicQueue q;
  for (uint32_t i = 0; i < 100; i++) q.push({i, false, 0, 0});
  CHECK(q.size() == PanicQueue::kCapacity);
  CHECK(q.front().atMs == 100 - PanicQueue::kCapacity);  // keeps the newest presses
}

int main() {
  RUN(pressWhileBusyIsLatchedOnce);
  RUN(bounceCountsOnce);
  RUN(millisWrapStillDebounces);
  RUN(queuedEventStaysUntilPosted);
  RUN(queueSurvivesReboot);
  RUN(queueIsBounded);
  return report("panic_latch_host_test");
}
