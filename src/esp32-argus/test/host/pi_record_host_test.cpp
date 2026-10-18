// Parsing one line pulled from the Pi (cv-argus's alerts/serialization.py to_json()).
// pi_record.{h,cpp} is the pure parser extracted from bluetooth_client.cpp so it can run here.
// Outcome::Drop means "ACK it and discard it": a record this firmware can never relay must not
// sit at the head of the Pi's queue forever (Top #3, EBT-03).
// SOURCES: pi_record.cpp
// NEEDS: arduinojson
#include <string>

#include "check.h"
#include "pi_record.h"

static const char* DROWSY =
    R"({"id":"a1","kind":"drowsiness","level":2,"created_at_ms":1792264812358,)"
    R"("mono_ms":5000123456,"boot_id":"b-1","clock_synced":true,"source_id":"cam0",)"
    R"("payload":{"class_name":"Drowsy","probabilities":[0.12,0.88],"transition":true},)"
    R"("geolocation":null})";

static void drowsyReading() {
  PiRecord r;
  CHECK(parsePiRecord(DROWSY, r) == ParseOutcome::Ok);
  CHECK(r.id == "a1");
  CHECK(r.kind == PiRecordKind::Reading);
  CHECK(r.state == ReadingState::Drowsy);
  CHECK(r.hasProbabilities);
  CHECK_NEAR(r.pDrowsy, 0.88, 1e-6);
  CHECK_NEAR(r.pNotDrowsy, 0.12, 1e-6);
  CHECK(r.transition);
}

static void sixtyFourBitTimesParseExactly() {
  PiRecord r;
  parsePiRecord(DROWSY, r);
  CHECK(r.createdAtMs == 1792264812358ULL);
  CHECK(r.hasMonoMs);
  CHECK(r.monoMs == 5000123456ULL);  // > 2^32
  CHECK(r.bootId == "b-1");
}

static void notDrowsyAndKeepalive() {
  PiRecord r;
  const char* line =
      R"({"id":"a2","kind":"drowsiness","level":1,"created_at_ms":1,"source_id":"cam0",)"
      R"("payload":{"class_name":"Not Drowsy","probabilities":[0.9,0.1],"transition":false}})";
  CHECK(parsePiRecord(line, r) == ParseOutcome::Ok);
  CHECK(r.state == ReadingState::NotDrowsy);
  CHECK(!r.transition);
}

static void noFace() {
  PiRecord r;
  const char* line =
      R"({"id":"a3","kind":"drowsiness","level":null,"created_at_ms":1,"source_id":"cam0",)"
      R"("payload":{"class_name":"No Face","probabilities":null,"transition":true}})";
  CHECK(parsePiRecord(line, r) == ParseOutcome::Ok);
  CHECK(r.state == ReadingState::NoFace);
  CHECK(!r.hasProbabilities);
}

static void routeStatus() {
  PiRecord r;
  const char* line =
      R"({"id":"h1","kind":"route_status","level":null,"created_at_ms":1,"source_id":"cam0",)"
      R"("payload":{"status":"OK"}})";
  CHECK(parsePiRecord(line, r) == ParseOutcome::Ok);
  CHECK(r.kind == PiRecordKind::RouteStatus);
}

static void unknownKindIsDroppedButAckable() {
  PiRecord r;
  const char* line = R"({"id":"x1","kind":"camera_status","level":null,"created_at_ms":1,)"
                     R"("source_id":"cam0","payload":{}})";
  CHECK(parsePiRecord(line, r) == ParseOutcome::Drop);
  CHECK(r.id == "x1");  // must be known so it can be ACKed away
}

static void missingOrBadProbabilitiesAreDropped() {
  // Old firmware read these as p(Drowsy)=0, i.e. a silent "Not Drowsy" (EBT-04).
  const char* missing = R"({"id":"p1","kind":"drowsiness","level":2,"created_at_ms":1,)"
                        R"("source_id":"c","payload":{"class_name":"Drowsy"}})";
  const char* wrongLength = R"({"id":"p2","kind":"drowsiness","level":2,"created_at_ms":1,)"
                            R"("source_id":"c","payload":{"class_name":"Drowsy","probabilities":[0.9]}})";
  const char* unknownClass = R"({"id":"p3","kind":"drowsiness","level":2,"created_at_ms":1,)"
                             R"("source_id":"c","payload":{"class_name":"Sleepy","probabilities":[0.1,0.9]}})";
  PiRecord r;
  CHECK(parsePiRecord(missing, r) == ParseOutcome::Drop);
  CHECK(r.id == "p1");
  CHECK(parsePiRecord(wrongLength, r) == ParseOutcome::Drop);
  CHECK(parsePiRecord(unknownClass, r) == ParseOutcome::Drop);
}

static void malformedJsonIsDroppedWithNoId() {
  PiRecord r;
  CHECK(parsePiRecord(R"({"id":"t1","kind":"drowsi)", r) == ParseOutcome::Drop);
  CHECK(r.id.empty());
  CHECK(parsePiRecord("", r) == ParseOutcome::Drop);
}

static void trailingCarriageReturnIsTolerated() {
  PiRecord r;
  std::string line = std::string(DROWSY) + "\r";
  CHECK(parsePiRecord(line, r) == ParseOutcome::Ok);
}

static void longPayloadLineParses() {
  std::string note(2000, 'x');
  std::string line = R"({"id":"l1","kind":"drowsiness","level":2,"created_at_ms":1,)"
                     R"("source_id":"c","payload":{"class_name":"Drowsy","probabilities":[0.1,0.9],)"
                     R"("note":")" + note + R"("}})";
  PiRecord r;
  CHECK(parsePiRecord(line, r) == ParseOutcome::Ok);
}

static void recordWithoutClockFieldsStillParses() {
  // Rows queued by an older cv-argus have no mono_ms/boot_id.
  PiRecord r;
  const char* line = R"({"id":"o1","kind":"drowsiness","level":2,"created_at_ms":7,)"
                     R"("source_id":"c","payload":{"class_name":"Drowsy","probabilities":[0.1,0.9]}})";
  CHECK(parsePiRecord(line, r) == ParseOutcome::Ok);
  CHECK(!r.hasMonoMs);
  CHECK(r.createdAtMs == 7);
}

int main() {
  RUN(drowsyReading);
  RUN(sixtyFourBitTimesParseExactly);
  RUN(notDrowsyAndKeepalive);
  RUN(noFace);
  RUN(routeStatus);
  RUN(unknownKindIsDroppedButAckable);
  RUN(missingOrBadProbabilitiesAreDropped);
  RUN(malformedJsonIsDroppedWithNoId);
  RUN(trailingCarriageReturnIsTolerated);
  RUN(longPayloadLineParses);
  RUN(recordWithoutClockFieldsStillParses);
  return report("pi_record_host_test");
}
