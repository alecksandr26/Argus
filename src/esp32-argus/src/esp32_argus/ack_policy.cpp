#include "ack_policy.h"

RelayAction relayActionFor(int status, bool /*bodyParsed*/) {
  if (status == 200 || status == 201) return RelayAction::Ack;
  if (status == 400 || status == 409 || status == 422) return RelayAction::AckAndDrop;
  if (status == 401 || status == 403) return RelayAction::HoldAndFlagAuth;
  if (status == 404) return RelayAction::RefetchRoute;
  return RelayAction::Hold;  // 5xx, 429, other 4xx, and every transport error (<= 0)
}

bool shouldAck(RelayAction action) {
  return action == RelayAction::Ack || action == RelayAction::AckAndDrop;
}

const char* relayActionName(RelayAction action) {
  switch (action) {
    case RelayAction::Ack: return "ack";
    case RelayAction::AckAndDrop: return "ack-and-drop";
    case RelayAction::Hold: return "hold";
    case RelayAction::HoldAndFlagAuth: return "hold (device key rejected)";
    case RelayAction::RefetchRoute: return "hold (ROUTE_ID not found)";
  }
  return "hold";
}
