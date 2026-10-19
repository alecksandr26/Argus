// Edge cases of parsing one line pulled from the Pi, on top of pi_record_host_test:
// - REC-19: cv-argus's json.dumps writes a NaN probability as a bare NaN token. ArduinoJson 7
//   rejects the whole line (InvalidInput, verified against 7.4.3), so today the row is never
//   ACKed and blocks the Pi's queue. The parser must still recover the id from the raw line so
//   the row can be ACKed away (Drop), and must never fuse it.
// - REC-20: clock_synced is parsed, so the relay can refuse to use created_at_ms as event time
//   when the Pi's clock was never NTP-synced this boot (no battery-backed clock).
// SOURCES: pi_record.cpp
// NEEDS: arduinojson
#include <string>

#include "check.h"
#include "pi_record.h"

static void nanProbabilitiesAreDroppedButAckable() {
  PiRecord r;
  const char* line =
      R"({"id":"n1","kind":"drowsiness","level":2,"created_at_ms":1,"source_id":"cam0",)"
      R"("payload":{"class_name":"Drowsy","probabilities":[NaN,NaN],"transition":true}})";
  CHECK(parsePiRecord(line, r) == ParseOutcome::Drop);
  CHECK(r.id == "n1");
}

static void infinityTokenIsDroppedButAckable() {
  PiRecord r;
  const char* line =
      R"({"id":"i1","kind":"drowsiness","level":2,"created_at_ms":1,"source_id":"cam0",)"
      R"("payload":{"class_name":"Drowsy","probabilities":[-Infinity,Infinity]}})";
  CHECK(parsePiRecord(line, r) == ParseOutcome::Drop);
  CHECK(r.id == "i1");
}

static void idIsNotRecoveredFromInsideThePayload() {
  // Only the top-level id is the record's id: a payload field named "id" must not be ACKed
  // instead (that would mark a different row sent on the Pi).
  PiRecord r;
  const char* line = R"({"payload":{"id":"wrong","probabilities":[NaN]},"id":"right",)"
                     R"("kind":"drowsiness"})";
  CHECK(parsePiRecord(line, r) == ParseOutcome::Drop);
  CHECK(r.id == "right");
}

static void clockSyncedFalseIsParsed() {
  PiRecord r;
  const char* line =
      R"({"id":"s1","kind":"route_status","level":null,"created_at_ms":1500000000000,)"
      R"("mono_ms":42,"boot_id":"b","clock_synced":false,"source_id":"cam0","payload":{"status":"OK"}})";
  CHECK(parsePiRecord(line, r) == ParseOutcome::Ok);
  CHECK(r.hasClockSynced);
  CHECK(!r.clockSynced);
}

static void clockSyncedMissingIsUnknownNotTrue() {
  // Rows from a cv-argus that predates the field: nothing says the clock was synced.
  PiRecord r;
  const char* line = R"({"id":"s2","kind":"route_status","level":null,"created_at_ms":1,)"
                     R"("source_id":"cam0","payload":{"status":"OK"}})";
  CHECK(parsePiRecord(line, r) == ParseOutcome::Ok);
  CHECK(!r.hasClockSynced);
  CHECK(!r.clockSynced);
}

int main() {
  RUN(nanProbabilitiesAreDroppedButAckable);
  RUN(infinityTokenIsDroppedButAckable);
  RUN(idIsNotRecoveredFromInsideThePayload);
  RUN(clockSyncedFalseIsParsed);
  RUN(clockSyncedMissingIsUnknownNotTrue);
  return report("pi_record_edge_host_test");
}
