// Parsers for the SIM800L responses the GPRS-first transport and the cell-location fallback
// depend on. Real response shapes from the SIM800 Series AT Command Manual / LBS app note.
// SOURCES: modem_parse.cpp
#include "check.h"
#include "modem_parse.h"

static void clbsFix() {
  // +CLBS: <locationcode>,<longitude>,<latitude>,<accuracy m> -- longitude FIRST.
  ClbsFix fix;
  CHECK(parseClbs("+CLBS: 0,-103.376134,20.658187,550", fix));
  CHECK(fix.ok);
  CHECK_NEAR(fix.lat, 20.658187, 1e-6);
  CHECK_NEAR(fix.lon, -103.376134, 1e-6);
  CHECK(fix.accuracyM == 550);
}

static void clbsNoFix() {
  ClbsFix fix;
  CHECK(parseClbs("+CLBS: 1", fix));  // location code 1 = failed
  CHECK(!fix.ok);
  CHECK(parseClbs("+CLBS: 2", fix));
  CHECK(!fix.ok);
}

static void clbsGarbage() {
  ClbsFix fix;
  CHECK(!parseClbs("ERROR", fix));
  CHECK(!parseClbs("+CLBS: 0,abc,20.6,550", fix));
  CHECK(!parseClbs("+CLBS: 0,-103.3", fix));
}

static void clbsRejectsZeroZero() {
  // Never relay 0,0 as a position (it's in the Gulf of Guinea).
  ClbsFix fix;
  CHECK(parseClbs("+CLBS: 0,0.000000,0.000000,0", fix));
  CHECK(!fix.ok);
}

static void httpAction() {
  HttpActionResult r;
  CHECK(parseHttpAction("+HTTPACTION: 1,201,57", r));
  CHECK(r.method == 1 && r.status == 201 && r.length == 57);
  CHECK(!r.isTransportError());
}

static void httpActionModuleErrorsAreTransportErrors() {
  HttpActionResult r;
  CHECK(parseHttpAction("+HTTPACTION: 1,601,0", r));  // 601 = network error
  CHECK(r.isTransportError());
  CHECK(parseHttpAction("+HTTPACTION: 1,603,0", r));  // 603 = DNS error
  CHECK(r.isTransportError());
  CHECK(parseHttpAction("+HTTPACTION: 1,408,0", r));  // a real HTTP 408 from the server
  CHECK(!r.isTransportError());
}

static void creg() {
  CregResult c;
  CHECK(parseCreg("+CREG: 0,1", c) && c.registered());   // home
  CHECK(parseCreg("+CREG: 0,5", c) && c.registered());   // roaming
  CHECK(parseCreg("+CREG: 0,2", c) && !c.registered());  // searching
  CHECK(parseCreg("+CREG: 0,3", c) && !c.registered());  // denied
  CHECK(!parseCreg("+CSQ: 20,0", c));
}

static void aLateResultFreesTheModem() {
  CHECK(lateActionVerdict(true, false, 1000, 130000) == LateAction::Free);
  CHECK(lateActionVerdict(true, true, 1000, 130000) == LateAction::Free);  // it came: use it
}

static void aBusyModemIsNotATransportFailure() {
  // Still waiting when the attempt runs out: Busy (modem_busy), not a timeout of the network.
  CHECK(lateActionVerdict(false, false, 9000, 130000) == LateAction::Busy);
  // A higher event arrived while waiting: stop, the request stays pending.
  CHECK(lateActionVerdict(false, true, 9000, 130000) == LateAction::Preempted);
  // Past the give-up age the result is never coming: free the session and go on.
  CHECK(lateActionVerdict(false, false, 130000, 130000) == LateAction::Free);
}

static void theWaitGoesInShortChunks() {
  CHECK(lateActionChunkMs(8000) == 500);
  CHECK(lateActionChunkMs(1400) == 400);  // keeps 1 s for the request itself
  CHECK(lateActionChunkMs(1000) == 0);
  CHECK(lateActionChunkMs(0) == 0);
  CHECK(lateActionChunkMs(5000, 200, 0) == 200);
}

int main() {
  RUN(clbsFix);
  RUN(clbsNoFix);
  RUN(clbsGarbage);
  RUN(clbsRejectsZeroZero);
  RUN(httpAction);
  RUN(httpActionModuleErrorsAreTransportErrors);
  RUN(creg);
  RUN(aLateResultFreesTheModem);
  RUN(aBusyModemIsNotATransportFailure);
  RUN(theWaitGoesInShortChunks);
  return report("modem_parse_host_test");
}
