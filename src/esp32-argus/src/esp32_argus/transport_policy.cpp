// TransportSelector and attemptTimeoutMs are header-only; this file holds the one out-of-line
// helper, and gives the host test's SOURCES line and the Arduino IDE's one-tab-per-module layout
// something to point at.
#include "transport_policy.h"

const char* transportName(Transport transport) {
  return transport == Transport::Wifi ? "wifi" : "gprs";
}
