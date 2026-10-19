#include "sms_module.h"

#include "config.h"
#include "modem.h"

// Raw AT commands via modem.h, deliberately not TinyGsm. This module only ever needs three
// things from the SIM800L (power it on, poll network registration, send one SMS), and TinyGsm
// is a general multi-modem TCP/GPRS abstraction built for far more than that; pulling in the
// whole library for three AT commands was most of this firmware's flash-size overrun. See
// src/esp32-argus/README.md's "Before you flash this" section.

namespace {
bool networkReady = false;
}  // namespace

void smsBegin() {
  Serial.println("[sms] Initializing SIM800L...");
  modemBegin();

  String info;
  modemSendAT("ATI", "OK", 2000UL, &info);
  Serial.print("[sms] Modem: ");
  Serial.println(info);
}

bool smsWaitForNetwork() {
  Serial.println("[sms] Waiting for GSM network...");
  unsigned long start = millis();
  do {
    if (modemIsRegistered()) {
      networkReady = true;
      break;
    }
    delay(2000);
  } while (millis() - start < SMS_NETWORK_WAIT_MS);

  Serial.println(networkReady ? "[sms] GSM network connected" : "[sms] No GSM network found");
  return networkReady;
}

bool smsSendPanicAlert(bool hasFix, double lat, double lon) {
  if (!networkReady && !smsWaitForNetwork()) {
    Serial.println("[sms] Skipping panic SMS -- no GSM network");
    return false;
  }

  String message = "EMERGENCIA\nUbicacion:\n";
  if (hasFix) {
    message += "https://www.google.com/maps?q=";
    message += String(lat, 6);
    message += ",";
    message += String(lon, 6);
  } else {
    message += "no disponible (sin senal GPS)";
  }

  Serial.println("[sms] Sending panic SMS...");
  modemSendAT("AT+CMGF=1", "OK", 2000UL);  // text mode -- needed every time, right before CMGS

  if (!modemSendAT("AT+CMGS=\"" SMS_DESTINATION_NUMBER "\"", ">", 5000UL)) {
    Serial.println("[sms] SMS FAILED -- modem never prompted for the message body");
    return false;
  }

  modemWrite(message);
  modemWriteByte(0x1A);  // Ctrl+Z -- submits the message to the SIM800L

  // No new AT command here -- the modem replies "+CMGS: <mr>\r\nOK\r\n" on its own once
  // the message is actually sent.
  bool ok = modemReadUntil("OK", 10000UL);
  Serial.println(ok ? "[sms] SMS sent" : "[sms] SMS FAILED");
  return ok;
}
