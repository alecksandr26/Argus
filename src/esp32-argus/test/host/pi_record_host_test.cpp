// Parsing one line pulled from the Pi (cv-argus's alerts/serialization.py to_json(), Bluetooth
// protocol v2: docs/designs/final-tweaks-contracts.md C1.4).
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

static void routeStatusIsGoneInV2AndAckedAway() {
  // v2 has no heartbeat (C1.4): a leftover route_status row is dropped, with its id so it is
  // ACKed off the Pi.
  PiRecord r;
  const char* line =
      R"({"id":"h1","kind":"route_status","level":null,"created_at_ms":1,"source_id":"cam0",)"
      R"("payload":{"status":"OK"}})";
  CHECK(parsePiRecord(line, r) == ParseOutcome::Drop);
  CHECK(r.id == "h1");
}

// The C1.4 example record, as the Pi serves it from `DLQ 1`.
static const char* V2_DEAD_LETTER =
    R"({"id":"3f2a9c00000000000000000000000001","kind":"drowsiness","level":2,)"
    R"("created_at_ms":1792764000123,"source_id":"pi_camera",)"
    R"("payload":{"class_name":"Drowsy","probabilities":[0.05,0.95],"transition":true,)"
    R"("severity":"critical","previous_severity":"medium","episode":"a1b2c3d4e5f6",)"
    R"("reason":"eyes_closed","model":"parts-rules-v1","trigger_s":3.2,)"
    R"("timers":{"eyes_closed":3.2,"head_down":0.0,"mouth_open":0.0,"no_face":0.0},"yawn_events":0},)"
    R"("geolocation":null,"mono_ms":81234567,"boot_id":"b","clock_synced":true,"age_s":41.4,)"
    R"("dlq":{"error":"no_network","body":{"client_event_id":"3f2a","kind":"alert"},)"
    R"("failed_at_ms":1792764060000,"attempts":2,"queued_s":37}})";

static void v2FieldsParse() {
  PiRecord r;
  CHECK(parsePiRecord(V2_DEAD_LETTER, r) == ParseOutcome::Ok);
  CHECK(r.severity == "critical");
  CHECK(r.previousSeverity == "medium");
  CHECK(r.episode == "a1b2c3d4e5f6");
  CHECK(r.reason == "eyes_closed");
  CHECK(r.model == "parts-rules-v1");
  CHECK(r.hasAgeS);
  CHECK_NEAR(r.ageS, 41.4, 1e-4);
  CHECK(r.isDeadLetter);
  CHECK(r.dlqError == "no_network");
  CHECK(r.dlqAttempts == 2);
  CHECK_NEAR(r.dlqQueuedS, 37.0, 1e-6);
  CHECK(r.dlqBody == R"({"client_event_id":"3f2a","kind":"alert"})");
}

static void ageSNullMissingOrNegativeIsUnknown() {
  // null = the Pi can't tell how old the event is (C1.4): the ESP32 FAILs an event too_old.
  const char* nullAge = R"({"id":"g1","kind":"drowsiness","level":1,"created_at_ms":1,)"
                        R"("source_id":"c","age_s":null,)"
                        R"("payload":{"class_name":"Not Drowsy","probabilities":[0.9,0.1]}})";
  const char* integerAge = R"({"id":"g2","kind":"drowsiness","level":1,"created_at_ms":1,)"
                           R"("source_id":"c","age_s":3,)"
                           R"("payload":{"class_name":"Not Drowsy","probabilities":[0.9,0.1]}})";
  const char* negativeAge = R"({"id":"g3","kind":"drowsiness","level":1,"created_at_ms":1,)"
                            R"("source_id":"c","age_s":-1.5,)"
                            R"("payload":{"class_name":"Not Drowsy","probabilities":[0.9,0.1]}})";
  PiRecord r;
  CHECK(parsePiRecord(nullAge, r) == ParseOutcome::Ok);
  CHECK(!r.hasAgeS);
  CHECK(parsePiRecord(DROWSY, r) == ParseOutcome::Ok);  // no age_s at all
  CHECK(!r.hasAgeS);
  CHECK(parsePiRecord(integerAge, r) == ParseOutcome::Ok);
  CHECK(r.hasAgeS);
  CHECK_NEAR(r.ageS, 3.0, 1e-6);
  CHECK(parsePiRecord(negativeAge, r) == ParseOutcome::Ok);
  CHECK(!r.hasAgeS);
}

static void deadLetterWithNullBody() {
  // esp32_unreachable rows never reached the ESP32, so the Pi has no composed body (C1.8).
  const char* line = R"({"id":"d1","kind":"drowsiness","level":null,"created_at_ms":1,)"
                     R"("source_id":"c","age_s":70.0,"payload":{"class_name":"No Face",)"
                     R"("probabilities":null,"transition":true,"severity":"critical",)"
                     R"("previous_severity":"none","episode":"e","reason":"no_face"},)"
                     R"("dlq":{"error":"esp32_unreachable","body":null,"failed_at_ms":5,)"
                     R"("attempts":0,"queued_s":0}})";
  PiRecord r;
  CHECK(parsePiRecord(line, r) == ParseOutcome::Ok);
  CHECK(r.isDeadLetter);
  CHECK(r.dlqBody.empty());
  CHECK(r.dlqAttempts == 0);
  CHECK(r.reason == "no_face");
}

static void unknownReasonPassesThrough() {
  // C1.4: unknown reasons are passed through as-is, not rejected.
  const char* line = R"({"id":"u1","kind":"drowsiness","level":2,"created_at_ms":1,)"
                     R"("source_id":"c","payload":{"class_name":"Drowsy","probabilities":[0.1,0.9],)"
                     R"("transition":true,"severity":"medium","reason":"phone_in_hand"}})";
  PiRecord r;
  CHECK(parsePiRecord(line, r) == ParseOutcome::Ok);
  CHECK(r.reason == "phone_in_hand");
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
  RUN(routeStatusIsGoneInV2AndAckedAway);
  RUN(v2FieldsParse);
  RUN(ageSNullMissingOrNegativeIsUnknown);
  RUN(deadLetterWithNullBody);
  RUN(unknownReasonPassesThrough);
  RUN(unknownKindIsDroppedButAckable);
  RUN(missingOrBadProbabilitiesAreDropped);
  RUN(malformedJsonIsDroppedWithNoId);
  RUN(trailingCarriageReturnIsTolerated);
  RUN(longPayloadLineParses);
  RUN(recordWithoutClockFieldsStillParses);
  return report("pi_record_host_test");
}
