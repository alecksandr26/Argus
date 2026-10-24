#include "gprs_http.h"

#include "config.h"
#include "modem.h"
#include "modem_parse.h"

namespace {

// The APN settings the open bearer was opened with, so new ones from the Pi reopen it.
String bearerApn;
String bearerUser;
String bearerPassword;
bool bearerOpen = false;  // verified open by AT+SAPBR=2,1 during the last attempt

// A request fired with AT+HTTPACTION whose attempt ended (deadline) before its result arrived.
// The modem's HTTP session stays busy until the "+HTTPACTION:" URC comes; gprsPoll() and the
// next attempt pick it up, then free the session with AT+HTTPTERM.
bool actionPending = false;
unsigned long actionFiredAt = 0;

// The AT steps of one attempt. Each step first asks whether to stop (a higher event pre-empts,
// or the deadline passed) and waits no longer than the deadline allows.
class Steps {
 public:
  Steps(const Deadline& deadline, bool (*preempt)()) : deadline_(deadline), preempt_(preempt) {}

  // False once the attempt must stop; why() says whether it was pre-empted or out of time.
  bool canContinue() {
    if (stopped_) return false;
    if (preempt_ && preempt_()) return stop(GprsEnd::Preempted);
    if (deadline_.expired(millis())) return stop(GprsEnd::Timeout);
    return true;
  }

  bool at(const String& cmd, const char* expect, unsigned long stepMs, String* resp = nullptr) {
    if (!canContinue()) return false;
    bool ok = modemSendAT(cmd, expect, deadline_.cap(millis(), stepMs), resp);
    if (!ok && deadline_.expired(millis())) stop(GprsEnd::Timeout);
    return ok;
  }

  bool atWithin(const String& cmd, const char* expect, unsigned long totalMs,
                String* resp = nullptr) {
    if (!canContinue()) return false;
    bool ok = modemSendATWithin(cmd, expect, deadline_.cap(millis(), totalMs), resp);
    if (!ok && deadline_.expired(millis())) stop(GprsEnd::Timeout);
    return ok;
  }

  bool stopped() const { return stopped_; }
  GprsEnd why() const { return why_; }
  uint32_t left() const { return deadline_.remaining(millis()); }

 private:
  bool stop(GprsEnd why) {
    stopped_ = true;
    why_ = why;
    return false;
  }

  const Deadline& deadline_;
  bool (*preempt_)();
  bool stopped_ = false;
  GprsEnd why_ = GprsEnd::TransportError;
};

// Bearer profile 1 carries every request. AT+SAPBR=2,1 answers "+SAPBR: 1,<status>,<ip>":
// 0 connecting, 1 connected, 2 closing, 3 closed. -1 if there was no readable answer.
int bearerStatus(Steps& s) {
  String resp;
  if (!s.at("AT+SAPBR=2,1", "OK", 3000UL, &resp)) return -1;
  int at = resp.indexOf("+SAPBR: 1,");
  if (at == -1) return -1;
  char c = resp.charAt(at + 10);
  return c >= '0' && c <= '3' ? c - '0' : -1;
}

bool ensureBearer(Steps& s, const DeviceCredentials& creds) {
  bool settingsChanged = creds.gprsApn != bearerApn || creds.gprsUser != bearerUser ||
                         creds.gprsPassword != bearerPassword;
  int status = bearerStatus(s);
  if (status == 1 && !settingsChanged) return true;
  if (s.stopped()) return false;
  if (status == 1) {
    Serial.println("[gprs] New APN settings from the Pi -- reopening the bearer");
    s.atWithin("AT+SAPBR=0,1", "OK", 65000UL);
    status = bearerStatus(s);
  }
  // An earlier attempt's activation (cut by its deadline) may still be under way: wait for it
  // rather than starting another.
  while (status == 0 && s.canContinue()) {
    vTaskDelay(pdMS_TO_TICKS(500));
    status = bearerStatus(s);
  }
  if (status == 1) return true;
  if (s.stopped()) return false;

  Serial.print("[gprs] Opening GPRS bearer, APN ");
  Serial.println(creds.gprsApn);
  s.at("AT+SAPBR=3,1,\"CONTYPE\",\"GPRS\"", "OK", 2000UL);
  s.at("AT+SAPBR=3,1,\"APN\",\"" + creds.gprsApn + "\"", "OK", 2000UL);
  if (creds.gprsUser.length() > 0) {
    s.at("AT+SAPBR=3,1,\"USER\",\"" + creds.gprsUser + "\"", "OK", 2000UL);
  }
  if (creds.gprsPassword.length() > 0) {
    s.at("AT+SAPBR=3,1,\"PWD\",\"" + creds.gprsPassword + "\"", "OK", 2000UL);
  }
  if (s.stopped()) return false;
  bearerApn = creds.gprsApn;
  bearerUser = creds.gprsUser;
  bearerPassword = creds.gprsPassword;
  // Signal and data attach say *why* a bearer won't open: CSQ 99 or below ~8 is too weak, and
  // "+CGATT: 0" means the network gives this SIM no packet data (no data plan, or no 2G data here).
  String reply;
  if (s.at("AT+CSQ", "OK", 2000UL, &reply)) {
    Serial.print("[gprs] Signal: ");
    Serial.println(modemOneLine(reply));
  }
  if (s.at("AT+CGATT?", "OK", 3000UL, &reply)) {
    Serial.print("[gprs] Data attach: ");
    Serial.println(modemOneLine(reply));
  }
  // Up to 85 s per the SIM800 manual, so a hard wait (an idle timeout never ends if the modem
  // keeps rebooting) -- and never past this attempt's deadline: if it is cut, the modem keeps
  // activating and the next attempt waits for it (status 0 above). It also answers ERROR if the
  // bearer raced open, so the status is re-checked instead of trusting this reply.
  unsigned long startedAt = millis();
  s.atWithin("AT+SAPBR=1,1", "OK", 85000UL, &reply);
  Serial.print("[gprs] AT+SAPBR=1,1 after ");
  Serial.print((millis() - startedAt) / 1000.0, 1);
  Serial.print(" s: ");
  Serial.println(reply.length() > 0 ? modemOneLine(reply) : String("(nothing yet)"));
  if (reply.indexOf("RDY") != -1 || reply.indexOf("Call Ready") != -1) {
    Serial.println("[gprs] The SIM800L rebooted while opening data -- usually its power supply "
                   "sagging under the ~2 A transmit bursts");
  }
  if (s.stopped()) return false;
  return bearerStatus(s) == 1;
}

// Reads the body of the last response: "+HTTPREAD: <n>\r\n<n bytes>\r\nOK". The answer is
// already in the modem's buffer, so this is a local read with its own short timeouts.
String readResponseBody() {
  if (!modemSendAT("AT+HTTPREAD", "+HTTPREAD:", 3000UL)) return String();
  String lengthLine;
  if (!modemReadUntil("\n", 1000UL, &lengthLine)) return String();
  int length = lengthLine.toInt();
  if (length <= 0) return String();
  String body = modemReadBytes((size_t)length, 3000UL);
  modemReadUntil("OK", 1000UL);
  return body;
}

void logLateAction(const String& urc) {
  HttpActionResult action;
  Serial.print("[gprs] Late result of a timed-out request: ");
  if (parseHttpAction(urc.c_str(), action)) {
    Serial.print(action.status);
    Serial.println(action.isTransportError()
                       ? " (a SIM800L network error: it never reached the backend)"
                       : " (it did reach the backend; its retry is a harmless duplicate)");
  } else {
    Serial.println(urc);
  }
}

void freeLateAction() {
  actionPending = false;
  modemSendAT("AT+HTTPTERM", "OK", 2000UL);
}

// Waits for a pending late result in short chunks (lateActionChunkMs, ~500 ms), asking between
// them whether to stop, so a live event can still pre-empt and the deadline still holds. With no
// Steps (gprsPoll, between attempts) it only checks what already arrived, without blocking.
LateAction settleLateAction(Steps* s) {
  if (!actionPending) return LateAction::Free;
  String urc;
  bool got = modemTakeHttpActionUrc(&urc);
  bool preempted = false;
  while (!got && s) {
    if (!s->canContinue()) {
      preempted = s->why() == GprsEnd::Preempted;
      break;
    }
    uint32_t chunk = lateActionChunkMs(s->left());
    if (chunk == 0) break;
    String buf;
    if (modemReadUntil("+HTTPACTION:", chunk, &buf)) {
      String rest;
      modemReadUntil("\n", 1000UL, &rest);
      urc = buf.substring(buf.indexOf("+HTTPACTION:")) + rest;
      got = true;
    }
  }
  LateAction verdict =
      lateActionVerdict(got, preempted, millis() - actionFiredAt, GPRS_LATE_ACTION_MAX_MS);
  if (verdict == LateAction::Free) {
    if (got) {
      logLateAction(urc);
    } else {
      Serial.println("[gprs] A timed-out request never got its result -- freeing the modem");
    }
    freeLateAction();
  }
  return verdict;
}

GprsResult finish(GprsResult r) {
  modemSetDeadline(nullptr);
  if (r.end != GprsEnd::Response && !actionPending) {
    modemSendAT("AT+HTTPTERM", "OK", 1000UL);  // frees a half-built request (short, local)
  }
  // ModemBusy and Preempted say nothing about the network: the bearer and registration stay.
  if (r.end == GprsEnd::TransportError || r.end == GprsEnd::Timeout) {
    bearerOpen = false;  // verify (and allow setup time) next attempt: the signal may be gone
    modemForgetRegistration();
  }
  return r;
}

}  // namespace

bool gprsNeedsSetup() { return !bearerOpen; }

void gprsPoll() {
  if (actionPending) settleLateAction(nullptr);
}

GprsResult gprsPostJson(const String& url, const DeviceCredentials& creds,
                        GprsBodyComposer compose, const Deadline& deadline, bool (*preempt)()) {
  GprsResult r;
  modemSetDeadline(&deadline);
  Steps s(deadline, preempt);

  // A request a timed-out attempt fired may still hold the modem's HTTP session.
  switch (settleLateAction(&s)) {
    case LateAction::Free: break;
    case LateAction::Preempted:
      r.end = GprsEnd::Preempted;
      return finish(r);
    case LateAction::Busy:
      Serial.println("[gprs] The modem is still busy with an earlier request (modem_busy)");
      r.end = GprsEnd::ModemBusy;
      return finish(r);
  }

  int stat = modemRegistrationCached(MODEM_REG_CACHE_MS);
  if (!modemStatRegistered(stat)) {
    Serial.print("[gprs] Not usable: ");
    Serial.println(modemRegistrationText(stat));
    r.end = GprsEnd::TransportError;
    return finish(r);
  }
  bearerOpen = ensureBearer(s, creds);
  if (!bearerOpen) {
    if (!s.stopped()) Serial.println("[gprs] Could not open the GPRS bearer");
    r.end = s.stopped() ? s.why() : GprsEnd::TransportError;
    return finish(r);
  }

  s.at("AT+HTTPTERM", "OK", 2000UL);  // clears a session a previous failure left open
  bool https = url.startsWith("https://");
  bool configured =
      s.at("AT+HTTPINIT", "OK", 5000UL) && s.at("AT+HTTPPARA=\"CID\",1", "OK", 2000UL) &&
      s.at("AT+HTTPPARA=\"URL\",\"" + url + "\"", "OK", 2000UL) &&
      s.at("AT+HTTPPARA=\"CONTENT\",\"application/json\"", "OK", 2000UL) &&
      s.at("AT+HTTPPARA=\"USERDATA\",\"X-Device-Api-Key: " + creds.apiKey + "\"", "OK",
           2000UL) &&
      s.at(https ? "AT+HTTPSSL=1" : "AT+HTTPSSL=0", "OK", 2000UL);
  if (!configured) {
    // AT+HTTPSSL=1 answering ERROR means this SIM800L's firmware was built without SSL.
    if (!s.stopped()) Serial.println("[gprs] Setting up the HTTP request failed (no SSL?)");
    r.end = s.stopped() ? s.why() : GprsEnd::TransportError;
    return finish(r);
  }
  // Composed now, right before it goes to the modem: the ages in it count every step above.
  if (!s.canContinue()) {
    r.end = s.why();
    return finish(r);
  }
  String body = compose();
  bool bodyLoaded =
      s.at("AT+HTTPDATA=" + String(body.length()) + ",10000", "DOWNLOAD", 5000UL);
  if (bodyLoaded) {
    modemWrite(body);
    bodyLoaded = modemReadUntil("OK", deadline.cap(millis(), 12000UL));
  }
  if (!bodyLoaded) {
    if (!s.stopped() && !deadline.expired(millis())) {
      Serial.println("[gprs] The modem didn't take the request body");
    }
    r.end = s.stopped() ? s.why() : deadline.expired(millis()) ? GprsEnd::Timeout
                                                               : GprsEnd::TransportError;
    return finish(r);
  }

  // The last point where a higher event can still pre-empt: after AT+HTTPACTION the request is on
  // its way and is never abandoned on purpose.
  if (!s.canContinue()) {
    r.end = s.why();
    return finish(r);
  }
  // "OK" comes back at once; the real result is the +HTTPACTION URC once the request is done (TLS
  // handshake + round trip over 2G). 1 = POST.
  if (!modemSendAT("AT+HTTPACTION=1", "OK", deadline.cap(millis(), 5000UL))) {
    r.end = deadline.expired(millis()) ? GprsEnd::Timeout : GprsEnd::TransportError;
    return finish(r);
  }
  actionPending = true;
  actionFiredAt = millis();
  String urc;
  if (!modemReadUntil("+HTTPACTION:", deadline.remaining(millis()), &urc)) {
    Serial.println("[gprs] No +HTTPACTION result before the deadline (picked up later)");
    r.end = GprsEnd::Timeout;
    return finish(r);
  }
  actionPending = false;
  modemSetDeadline(nullptr);  // the answer is in the modem: reading it is local
  String rest;
  modemReadUntil("\n", 1000UL, &rest);
  urc += rest;
  HttpActionResult action;
  if (!parseHttpAction(urc.c_str(), action) || action.isTransportError()) {
    Serial.print("[gprs] Request failed: ");
    Serial.println(modemOneLine(urc));
    r.end = GprsEnd::TransportError;
    return finish(r);
  }
  r.end = GprsEnd::Response;
  r.status = action.status;
  if (action.length > 0) r.body = readResponseBody();
  modemSendAT("AT+HTTPTERM", "OK", 2000UL);
  return finish(r);
}
