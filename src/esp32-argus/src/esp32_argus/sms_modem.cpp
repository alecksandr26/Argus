#include "sms_modem.h"

#include "config.h"
#include "device_config.h"  // maskPhone
#include "modem.h"
#include "modem_parse.h"
#include "transport_policy.h"

namespace {

const uint8_t CTRL_Z = 0x1A;  // submits the text
const uint8_t ESC = 0x1B;     // abandons a text the modem is waiting for

const unsigned int REPLY_SNIPPET_MAX = 80;  // the end of a long reply: where the error is

// "<step>: <the modem's reply on one line>", for the log. The phone never shows in full: an echo
// of AT+CMGS="<phone>" (echo back on after a modem reset) is masked like every other log line.
String stepError(const char* step, const String& reply, const String& phone) {
  String e(step);
  e += ": ";
  if (reply.length() == 0) {
    e += "no answer";
    return e;
  }
  String line = modemOneLine(reply);
  line.replace(phone, maskPhone(phone));
  if (line.length() > REPLY_SNIPPET_MAX) {
    line = String("...") + line.substring(line.length() - REPLY_SNIPPET_MAX);
  }
  e += line;
  return e;
}

}  // namespace

bool smsSendText(const String& phone, const String& text, String& error, int* reference) {
  if (reference) *reference = -1;
  // A hard bound for the whole exchange, on top of each step's idle timeout: a modem that keeps
  // printing unrelated lines (RING, +CREG...) can't hold the sender task past it.
  Deadline deadline(millis(), SMS_SEND_TIMEOUT_MS + 10000UL);
  modemSetDeadline(&deadline);
  String reply;
  bool ok = false;
  if (!modemSendATKeepingUrcs("AT+CMGF=1", "OK", 2000UL, &reply)) {  // text mode, every time
    error = stepError("AT+CMGF=1", reply, phone);
  } else if (!modemSendATKeepingUrcs("AT+CSCS=\"GSM\"", "OK", 2000UL, &reply)) {
    error = stepError("AT+CSCS", reply, phone);
  } else if (!modemSendATKeepingUrcs("AT+CMGS=\"" + phone + "\"", ">", 5000UL, &reply)) {
    modemWriteByte(ESC);  // in case the prompt came late: never leave the modem in text input
    error = stepError("AT+CMGS (no '>' prompt)", reply, phone);
  } else {
    modemWrite(text);
    modemWriteByte(CTRL_Z);
    // No new command here: the modem answers "+CMGS: <mr>" and then OK on its own once the
    // network took the message (a few seconds over 2G, up to a minute).
    if (!modemReadUntilKeepingUrcs("+CMGS:", SMS_SEND_TIMEOUT_MS, &reply)) {
      error = stepError("waiting for +CMGS", reply, phone);  // "+CMS ERROR: <n>" ends it early
    } else {
      // The read stops right at "+CMGS:": the reference number comes with the rest of the line.
      String rest;
      modemReadUntilKeepingUrcs("OK", 5000UL, &rest);
      if (reference) *reference = parseCmgsReference((reply + rest).c_str());
      ok = true;
    }
  }
  modemSetDeadline(nullptr);
  return ok;
}
