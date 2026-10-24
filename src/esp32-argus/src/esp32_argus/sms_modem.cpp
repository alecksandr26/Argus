#include "sms_modem.h"

#include "config.h"
#include "modem.h"
#include "transport_policy.h"

namespace {

const uint8_t CTRL_Z = 0x1A;  // submits the text
const uint8_t ESC = 0x1B;     // abandons a text the modem is waiting for

String stepError(const char* step, const String& reply) {
  String e(step);
  e += ": ";
  e += reply.length() > 0 ? modemOneLine(reply) : String("no answer");
  return e;
}

}  // namespace

bool smsSendText(const String& phone, const String& text, String& error) {
  // A hard bound for the whole exchange, on top of each step's idle timeout: a modem that keeps
  // printing unrelated lines (RING, +CREG...) can't hold the sender task past it.
  Deadline deadline(millis(), SMS_SEND_TIMEOUT_MS + 10000UL);
  modemSetDeadline(&deadline);
  String reply;
  bool ok = false;
  if (!modemSendATKeepingUrcs("AT+CMGF=1", "OK", 2000UL, &reply)) {  // text mode, every time
    error = stepError("AT+CMGF=1", reply);
  } else if (!modemSendATKeepingUrcs("AT+CSCS=\"GSM\"", "OK", 2000UL, &reply)) {
    error = stepError("AT+CSCS", reply);
  } else if (!modemSendATKeepingUrcs("AT+CMGS=\"" + phone + "\"", ">", 5000UL, &reply)) {
    modemWriteByte(ESC);  // in case the prompt came late: never leave the modem in text input
    error = stepError("AT+CMGS (no '>' prompt)", reply);
  } else {
    modemWrite(text);
    modemWriteByte(CTRL_Z);
    // No new command here: the modem answers "+CMGS: <mr>" and then OK on its own once the
    // network took the message (a few seconds over 2G, up to a minute).
    if (!modemReadUntilKeepingUrcs("+CMGS:", SMS_SEND_TIMEOUT_MS, &reply)) {
      error = stepError("waiting for +CMGS", reply);  // "+CMS ERROR: <n>" ends it early
    } else {
      modemReadUntilKeepingUrcs("OK", 5000UL);
      ok = true;
    }
  }
  modemSetDeadline(nullptr);
  return ok;
}
