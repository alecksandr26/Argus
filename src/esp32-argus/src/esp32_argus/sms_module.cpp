#include "sms_module.h"

#include "config.h"

// Raw AT commands over the modem's own HardwareSerial -- deliberately not TinyGsm. This
// device only ever needs three things from the SIM800L (power it on, poll network
// registration, send one SMS), and TinyGsm is a general multi-modem TCP/GPRS abstraction
// built for far more than that; pulling in the whole library for three AT commands was
// most of this firmware's flash-size overrun. See src/esp32-argus/README.md's "Before you
// flash this" section.

namespace {
HardwareSerial modemSerial(1);
bool networkReady = false;

// Drains anything already waiting on the modem UART (stray boot banners, unsolicited
// "RDY"/"+CPIN: READY" lines) so the next exchange starts clean.
void modemFlush() {
  while (modemSerial.available()) modemSerial.read();
}

// Reads whatever the modem sends back, with an *idle* timeout (resets every time a byte
// arrives) -- the same millis()-rollover-safe pattern bluetooth_client.cpp's readLine()
// uses, not an absolute `millis() + timeoutMs` deadline. Stops early on "ERROR" too, so a
// rejected command doesn't sit out the full timeout.
bool readUntil(const char* expect, unsigned long timeoutMs, String* response = nullptr) {
  String buf;
  unsigned long lastByteAt = millis();
  while (millis() - lastByteAt < timeoutMs) {
    while (modemSerial.available()) {
      buf += (char)modemSerial.read();
      lastByteAt = millis();
      if (buf.indexOf(expect) != -1) {
        if (response) *response = buf;
        return true;
      }
      if (buf.indexOf("ERROR") != -1) {
        if (response) *response = buf;
        return false;
      }
    }
  }
  if (response) *response = buf;
  return false;
}

bool sendAT(const char* cmd, const char* expect, unsigned long timeoutMs,
            String* response = nullptr) {
  modemFlush();
  modemSerial.print(cmd);
  modemSerial.print("\r\n");
  return readUntil(expect, timeoutMs, response);
}

}  // namespace

void smsBegin() {
  pinMode(MODEM_PWKEY, OUTPUT);
  pinMode(MODEM_RST, OUTPUT);
  pinMode(MODEM_POWER_ON, OUTPUT);

  digitalWrite(MODEM_PWKEY, LOW);
  digitalWrite(MODEM_RST, HIGH);
  digitalWrite(MODEM_POWER_ON, HIGH);

  modemSerial.begin(9600, SERIAL_8N1, MODEM_RX, MODEM_TX);
  delay(3000);

  Serial.println("[sms] Initializing SIM800L...");
  // The modem is often still settling right after power-on and misses the first poke.
  sendAT("AT", "OK", 2000UL);
  sendAT("AT", "OK", 2000UL);
  sendAT("ATE0", "OK", 2000UL);  // echo off, so later reads can't false-match on our own
                                  // echoed command text

  String info;
  sendAT("ATI", "OK", 2000UL, &info);
  Serial.print("[sms] Modem: ");
  Serial.println(info);
}

bool smsWaitForNetwork() {
  Serial.println("[sms] Waiting for GSM network...");
  unsigned long start = millis();
  do {
    String resp;
    // +CREG: <n>,<stat> -- stat 1 = registered (home), 5 = registered (roaming).
    if (sendAT("AT+CREG?", "+CREG:", 2000UL, &resp) &&
        (resp.indexOf(",1") != -1 || resp.indexOf(",5") != -1)) {
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
  sendAT("AT+CMGF=1", "OK", 2000UL);  // text mode -- needed every time, right before CMGS

  if (!sendAT("AT+CMGS=\"" SMS_DESTINATION_NUMBER "\"", ">", 5000UL)) {
    Serial.println("[sms] SMS FAILED -- modem never prompted for the message body");
    return false;
  }

  modemSerial.print(message);
  modemSerial.write(0x1A);  // Ctrl+Z -- submits the message to the SIM800L

  // No new AT command here -- the modem replies "+CMGS: <mr>\r\nOK\r\n" on its own once
  // the message is actually sent.
  bool ok = readUntil("OK", 10000UL);
  Serial.println(ok ? "[sms] SMS sent" : "[sms] SMS FAILED");
  return ok;
}
