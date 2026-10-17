#define TINY_GSM_MODEM_SIM800
#include <TinyGsmClient.h>

#include "sms_module.h"

#include "config.h"

namespace {
HardwareSerial modemSerial(1);
TinyGsm modem(modemSerial);
bool networkReady = false;
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
  modem.restart();
  Serial.print("[sms] Modem: ");
  Serial.println(modem.getModemInfo());
}

bool smsWaitForNetwork() {
  Serial.println("[sms] Waiting for GSM network...");
  networkReady = modem.waitForNetwork(SMS_NETWORK_WAIT_MS);
  Serial.println(networkReady ? "[sms] GSM network connected" : "[sms] No GSM network found");
  return networkReady;
}

bool smsSendPanicAlert(bool hasFix, double lat, double lon) {
  if (!networkReady && !modem.isNetworkConnected()) {
    if (!smsWaitForNetwork()) {
      Serial.println("[sms] Skipping panic SMS -- no GSM network");
      return false;
    }
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
  bool ok = modem.sendSMS(SMS_DESTINATION_NUMBER, message);
  Serial.println(ok ? "[sms] SMS sent" : "[sms] SMS FAILED");
  return ok;
}
