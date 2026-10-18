// What to do with a pulled record after trying to relay it, by HTTP result. Negative statuses are
// transport errors (HTTPClient's codes, or the SIM800L's 6xx mapped to -1 by gprs_http).
// Today every non-2xx is "don't ACK", so a permanently rejected record blocks the queue head
// forever (Top #3, NET-01) and a 201 with an unparseable body is re-posted (NET-02).
// SOURCES: ack_policy.cpp
#include "ack_policy.h"
#include "check.h"

static void successAcks() {
  CHECK(relayActionFor(201, true) == RelayAction::Ack);
  CHECK(relayActionFor(200, true) == RelayAction::Ack);  // idempotent duplicate (client_event_id)
}

static void createdWithUnparseableBodyStillAcks() {
  CHECK(relayActionFor(201, false) == RelayAction::Ack);
}

static void permanentRejectionsAreDropped() {
  CHECK(relayActionFor(400, true) == RelayAction::AckAndDrop);
  CHECK(relayActionFor(409, true) == RelayAction::AckAndDrop);  // route finished
  CHECK(relayActionFor(422, true) == RelayAction::AckAndDrop);  // invalid body
}

static void authFailuresHoldAndFlag() {
  CHECK(relayActionFor(401, true) == RelayAction::HoldAndFlagAuth);
  CHECK(relayActionFor(403, true) == RelayAction::HoldAndFlagAuth);
}

static void unknownRouteRefetchesTheActiveRoute() {
  CHECK(relayActionFor(404, true) == RelayAction::RefetchRoute);
}

static void serverAndTransportErrorsHold() {
  CHECK(relayActionFor(500, true) == RelayAction::Hold);
  CHECK(relayActionFor(503, true) == RelayAction::Hold);
  CHECK(relayActionFor(429, true) == RelayAction::Hold);
  CHECK(relayActionFor(-1, false) == RelayAction::Hold);   // connection refused
  CHECK(relayActionFor(-11, false) == RelayAction::Hold);  // read timeout
  CHECK(relayActionFor(0, false) == RelayAction::Hold);
}

static void onlyAckActionsAck() {
  CHECK(shouldAck(RelayAction::Ack));
  CHECK(shouldAck(RelayAction::AckAndDrop));
  CHECK(!shouldAck(RelayAction::Hold));
  CHECK(!shouldAck(RelayAction::HoldAndFlagAuth));
  CHECK(!shouldAck(RelayAction::RefetchRoute));
}

int main() {
  RUN(successAcks);
  RUN(createdWithUnparseableBodyStillAcks);
  RUN(permanentRejectionsAreDropped);
  RUN(authFailuresHoldAndFlag);
  RUN(unknownRouteRefetchesTheActiveRoute);
  RUN(serverAndTransportErrorsHold);
  RUN(onlyAckActionsAck);
  return report("ack_policy_host_test");
}
