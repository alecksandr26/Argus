#include "gprs_http.h"

#include "config.h"
#include "credentials.h"
#include "modem.h"
#include "modem_parse.h"

namespace {

constexpr int kTransportError = -1;

// The APN settings the open bearer was opened with, so new ones from the Pi reopen it.
String bearerApn;
String bearerUser;
String bearerPassword;

// Bearer profile 1 carries every request. AT+SAPBR=2,1 answers "+SAPBR: 1,<status>,<ip>",
// status 1 = connected.
bool bearerIsOpen() {
  String resp;
  if (!modemSendAT("AT+SAPBR=2,1", "OK", 3000UL, &resp)) return false;
  int at = resp.indexOf("+SAPBR: 1,");
  return at != -1 && resp.charAt(at + 10) == '1';
}

bool ensureBearer() {
  const DeviceCredentials& creds = credentials();
  bool settingsChanged = creds.gprsApn != bearerApn || creds.gprsUser != bearerUser ||
                         creds.gprsPassword != bearerPassword;
  bool open = bearerIsOpen();
  if (open && !settingsChanged) return true;
  if (open) {
    Serial.println("[gprs] New APN settings from the Pi -- reopening the bearer");
    modemSendAT("AT+SAPBR=0,1", "OK", 65000UL);
  }

  Serial.print("[gprs] Opening GPRS bearer, APN ");
  Serial.println(creds.gprsApn);
  modemSendAT("AT+SAPBR=3,1,\"CONTYPE\",\"GPRS\"", "OK", 2000UL);
  modemSendAT("AT+SAPBR=3,1,\"APN\",\"" + creds.gprsApn + "\"", "OK", 2000UL);
  if (creds.gprsUser.length() > 0) {
    modemSendAT("AT+SAPBR=3,1,\"USER\",\"" + creds.gprsUser + "\"", "OK", 2000UL);
  }
  if (creds.gprsPassword.length() > 0) {
    modemSendAT("AT+SAPBR=3,1,\"PWD\",\"" + creds.gprsPassword + "\"", "OK", 2000UL);
  }
  bearerApn = creds.gprsApn;
  bearerUser = creds.gprsUser;
  bearerPassword = creds.gprsPassword;
  // Signal and data attach say *why* a bearer won't open: CSQ 99 or below ~8 is too weak, and
  // "+CGATT: 0" means the network gives this SIM no packet data (no data plan, or no 2G data here).
  String reply;
  modemSendAT("AT+CSQ", "OK", 2000UL, &reply);
  Serial.print("[gprs] Signal: ");
  Serial.println(modemOneLine(reply));
  modemSendAT("AT+CGATT?", "OK", 10000UL, &reply);
  Serial.print("[gprs] Data attach: ");
  Serial.println(modemOneLine(reply));
  // Can take up to 85 s per the SIM800 manual, so a hard deadline (an idle timeout never ends if
  // the modem keeps rebooting). It also answers ERROR if the bearer raced open, so re-check the
  // status instead of trusting this reply.
  Serial.println("[gprs] Activating the data session (AT+SAPBR=1,1, up to 85 s)...");
  unsigned long startedAt = millis();
  modemSendATWithin("AT+SAPBR=1,1", "OK", 85000UL, &reply);
  Serial.print("[gprs] Modem replied after ");
  Serial.print((millis() - startedAt) / 1000);
  Serial.print(" s: ");
  Serial.println(reply.length() > 0 ? modemOneLine(reply) : String("(nothing)"));
  if (reply.indexOf("RDY") != -1 || reply.indexOf("Call Ready") != -1) {
    Serial.println("[gprs] The SIM800L rebooted while opening data -- usually its power supply "
                   "sagging under the ~2 A transmit bursts");
  }
  open = bearerIsOpen();
  Serial.println(open ? "[gprs] Bearer open" : "[gprs] Could not open the GPRS bearer");
  return open;
}

// Reads the body of the last response: "+HTTPREAD: <n>\r\n<n bytes>\r\nOK".
String readResponseBody() {
  if (!modemSendAT("AT+HTTPREAD", "+HTTPREAD:", 10000UL)) return String();
  String lengthLine;
  if (!modemReadUntil("\n", 2000UL, &lengthLine)) return String();
  int length = lengthLine.toInt();
  if (length <= 0) return String();
  String body = modemReadBytes((size_t)length, 10000UL);
  modemReadUntil("OK", 2000UL);
  return body;
}

}  // namespace

int gprsHttpRequest(const char* method, const String& url, const String& apiKey,
                    const String& body, String* responseBody) {
  bool isPost = strcmp(method, "POST") == 0;
  if (!isPost && strcmp(method, "GET") != 0) return kTransportError;  // no PUT on the SIM800L
  if (!modemIsRegistered()) {
    Serial.println("[gprs] Not registered on the mobile network");
    return kTransportError;
  }
  if (!ensureBearer()) return kTransportError;

  modemSendAT("AT+HTTPTERM", "OK", 2000UL);  // clears a session a previous failure left open
  if (!modemSendAT("AT+HTTPINIT", "OK", 5000UL)) {
    Serial.println("[gprs] AT+HTTPINIT failed");
    return kTransportError;
  }

  int status = kTransportError;
  bool https = url.startsWith("https://");
  bool configured =
      modemSendAT("AT+HTTPPARA=\"CID\",1", "OK", 2000UL) &&
      modemSendAT("AT+HTTPPARA=\"URL\",\"" + url + "\"", "OK", 2000UL) &&
      modemSendAT("AT+HTTPPARA=\"CONTENT\",\"application/json\"", "OK", 2000UL) &&
      modemSendAT("AT+HTTPPARA=\"USERDATA\",\"X-Device-Api-Key: " + apiKey + "\"", "OK",
                  2000UL) &&
      modemSendAT(https ? "AT+HTTPSSL=1" : "AT+HTTPSSL=0", "OK", 2000UL);

  bool bodyLoaded = false;
  if (!configured) {
    // AT+HTTPSSL=1 answering ERROR means this SIM800L's firmware was built without SSL.
    Serial.println("[gprs] Configuring the HTTP request failed (no SSL support in the modem?)");
  } else if (!isPost) {
    bodyLoaded = true;  // a GET has no body to upload
  } else if (!modemSendAT("AT+HTTPDATA=" + String(body.length()) + ",10000", "DOWNLOAD",
                          5000UL)) {
    Serial.println("[gprs] Modem never asked for the request body");
  } else {
    modemWrite(body);
    bodyLoaded = modemReadUntil("OK", 12000UL);
  }

  if (bodyLoaded) {
    // "OK" comes back at once; the real result is the +HTTPACTION URC once the request is
    // done (TLS handshake + round trip over 2G, so allow a long wait). 0 = GET, 1 = POST.
    String urc;
    if (modemSendAT(isPost ? "AT+HTTPACTION=1" : "AT+HTTPACTION=0", "OK", 5000UL) &&
        modemReadUntil("+HTTPACTION:", 120000UL, &urc)) {
      String rest;
      modemReadUntil("\n", 2000UL, &rest);
      urc += rest;
      HttpActionResult action;
      if (parseHttpAction(urc.c_str(), action) && !action.isTransportError()) {
        status = action.status;
        if (responseBody && action.length > 0) *responseBody = readResponseBody();
      } else {
        Serial.print("[gprs] Request failed: ");
        Serial.println(urc);
      }
    } else {
      Serial.println("[gprs] No +HTTPACTION result from the modem");
    }
  }

  modemSendAT("AT+HTTPTERM", "OK", 2000UL);
  return status;
}
