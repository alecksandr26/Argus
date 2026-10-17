// Fusion runs on the Pi's timeline (each record's mono_ms), not on when the ESP32 happened to
// process it (FUS-02). PiClock maps ESP32 millis() onto that timeline for the local grip tick,
// survives millis() wrap (FUS-09), and resets when the Pi reboots (boot_id changes).
// RecentIds drops records re-served after a lost ACK (EBT-07).
// SOURCES: pi_clock.cpp
#include "check.h"
#include "pi_clock.h"

static void mapsLocalTimeOntoPiTimeline() {
  PiClock c;
  CHECK(!c.synced());
  c.observe("boot-1", /*piMonoMs=*/5000000000ULL, /*localMs=*/1000);
  CHECK(c.synced());
  CHECK_NEAR(c.piSecondsAt(1000), 5000000.0, 1e-6);
  CHECK_NEAR(c.piSecondsAt(3500), 5000002.5, 1e-6);
}

static void survivesMillisWrap() {
  PiClock c;
  c.observe("boot-1", 1000000ULL, 0xFFFFFC18u);  // 1000 ms before the wrap
  CHECK_NEAR(c.piSecondsAt(0x000003E8u), 1002.0, 1e-6);  // 2000 ms later, across it
}

static void piRebootResetsTheMapping() {
  PiClock c;
  c.observe("boot-1", 9000000ULL, 1000);
  CHECK(c.observe("boot-2", 2000ULL, 5000));  // returns true: the Pi rebooted
  CHECK_NEAR(c.piSecondsAt(5000), 2.0, 1e-6);
}

static void olderRecordDoesNotMoveTheMapping() {
  PiClock c;
  c.observe("boot-1", 10000ULL, 1000);
  c.observe("boot-1", 4000ULL, 2000);  // a backlog record: only the newest defines "now"
  CHECK_NEAR(c.piSecondsAt(1000), 10.0, 1e-6);
}

static void recentIdsDropsDuplicates() {
  RecentIds ids(4);
  CHECK(!ids.seen("a"));
  CHECK(ids.seen("a"));
  CHECK(!ids.seen("b"));
  for (const char* id : {"c", "d", "e", "f"}) ids.seen(id);
  CHECK(!ids.seen("a"));  // evicted: bounded memory
}

int main() {
  RUN(mapsLocalTimeOntoPiTimeline);
  RUN(survivesMillisWrap);
  RUN(piRebootResetsTheMapping);
  RUN(olderRecordDoesNotMoveTheMapping);
  RUN(recentIdsDropsDuplicates);
  return report("pi_clock_host_test");
}
