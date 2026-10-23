// The fixed-capacity strings of the sender's static slots (bounded_string.h), and the check that
// the longest event JSON C2.1 allows still fits a dead-letter body slot.
// SOURCES: backend_messages.cpp pi_event_policy.cpp
// NEEDS: arduinojson
#include <string>

#include "backend_messages.h"
#include "bounded_string.h"
#include "check.h"

static void assignsAndComparesLikeAString() {
  BoundedString<8> s;
  CHECK(s.empty() && s.size() == 0 && std::string(s.c_str()).empty());
  CHECK(s.assign("abc"));
  CHECK(s == "abc" && s == std::string("abc") && std::string("abc") == s && "abc" == s);
  CHECK(s != "abcd" && s != std::string("ab"));
  std::string copy = s;
  CHECK(copy == "abc");
  CHECK(std::string("x-") + s == "x-abc");
  s = std::string("1234567");  // exactly the capacity
  CHECK(s.size() == 7 && s == "1234567");
  BoundedString<8> t = s;
  CHECK(t == s);
  s.clear();
  CHECK(s.empty());
}

static void tooLongTextIsRefusedNotCut() {
  BoundedString<8> s("keep");
  CHECK(!s.assign("12345678"));  // 8 chars don't fit (7 + NUL)
  CHECK(s.empty());               // left empty: a cut id would be a different id
  s = "ok";
  s = std::string(20, 'x');
  CHECK(s.empty());
  CHECK(s.assign(nullptr));
  CHECK(s.empty());
}

static void assignTruncatedCuts() {
  BoundedString<5> s;
  CHECK(s.assignTruncated(std::string("abc")));
  CHECK(s == "abc");
  CHECK(!s.assignTruncated(std::string("abcdefg")));
  CHECK(s == "abcd");
}

static ReportEvent longestEvent() {
  ReportEvent e;
  CHECK(e.clientEventId.assign(std::string(64, 'c')));
  CHECK(e.episode.assign(std::string(64, 'e')));
  CHECK(e.severity.assign("critical"));
  CHECK(e.alertType.assign(std::string(64, 't')));
  CHECK(e.model.assign(std::string(ModelString::kMaxLength, 'm')));
  e.position.known = true;
  e.position.lat = -89.1234567;
  e.position.lon = -179.1234567;
  e.position.lastKnown = true;
  e.position.locationAgeS = 604799.9f;
  e.hasSpeed = true;
  e.speedKmh = 199.9f;
  e.hasScores = true;
  e.pNotDrowsy = 0.123456789f;
  e.pDrowsy = 0.876543211f;
  return e;
}

static void theLongestEventJsonFitsADeadLetterSlot() {
  std::string json = composeEventJson(longestEvent(), 604799.9f, EventDelivery::Dlq, 604799.9f);
  std::printf("longest event JSON: %zu B (slot %zu)\n", json.size(), EventJsonString::kMaxLength);
  EventJsonString slot;
  CHECK(slot.assign(json));
  CHECK(slot == json);
}

static void aRecordTooLongForC2DoesNotFit() {
  PiRecord r;
  r.id = std::string(64, 'a');
  r.episode = "ep";
  r.reason = "eyes_closed";
  CHECK(recordFitsEvent(r));
  r.id += "a";
  CHECK(!recordFitsEvent(r));
  r.id = "id";
  r.episode = std::string(65, 'e');
  CHECK(!recordFitsEvent(r));
  r.episode = "";  // no episode: the record id stands in for it
  CHECK(recordFitsEvent(r));
  r.reason = std::string(65, 'r');
  CHECK(!recordFitsEvent(r));
  r.reason = "";
  r.className = "Drowsy";  // no reason: the class stands in for it
  CHECK(recordFitsEvent(r));
  r.id = "";
  CHECK(!recordFitsEvent(r));
}

static void aLongModelNameIsCutNotDropped() {
  PiRecord r;
  r.id = "id1";
  r.reason = "eyes_closed";
  r.model = std::string(80, 'm');
  ReportEvent e = eventFromRecord(r, PiEventKind::AlertCritical, EventPosition(), false, 0.0f);
  CHECK(e.model.size() == ModelString::kMaxLength);
  CHECK(e.clientEventId == "id1");
  CHECK(e.episode == "id1");
}

int main() {
  RUN(assignsAndComparesLikeAString);
  RUN(tooLongTextIsRefusedNotCut);
  RUN(assignTruncatedCuts);
  RUN(theLongestEventJsonFitsADeadLetterSlot);
  RUN(aRecordTooLongForC2DoesNotFit);
  RUN(aLongModelNameIsCutNotDropped);
  return report("bounded_string_host_test");
}
