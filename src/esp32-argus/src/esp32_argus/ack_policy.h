#pragma once

// What to do with a pulled Pi record after trying to relay it, decided by the HTTP result.
// Pure logic (no Arduino deps) so test/host/ack_policy_host_test.cpp can run it on the PC.
//
// `status` is the HTTP status, or <= 0 for a transport error (HTTPClient's negative codes, or
// a SIM800L 6xx mapped to -1 by gprs_http). `bodyParsed` doesn't change the action: a 201
// whose body couldn't be parsed was still stored, so re-posting it would only duplicate it.

enum class RelayAction {
  Ack,              // stored (201), or already stored (200 = client_event_id retry)
  AckAndDrop,       // permanently rejected (400/409/422) -- retrying can never succeed, and
                    // leaving it unacked would block every record behind it on the Pi
  Hold,             // transient (5xx, 429, transport error) -- leave unacked, retry next pull
  HoldAndFlagAuth,  // 401/403 -- the device key is wrong; holding keeps the data until fixed
  RefetchRoute,     // 404 -- ROUTE_ID doesn't exist on this backend
};

RelayAction relayActionFor(int status, bool bodyParsed);
bool shouldAck(RelayAction action);
const char* relayActionName(RelayAction action);
