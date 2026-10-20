#include "bluetooth_client.h"

#include <ArduinoJson.h>
#include <BluetoothSerial.h>

#include "config.h"

namespace {
BluetoothSerial SerialBT;
uint8_t piMac[6] = PI_BT_MAC_ADDRESS;

// Link bookkeeping, for the Serial log only.
bool linkUp = false;
bool linkEverTried = false;
unsigned long lastAnsweredAt = 0;
unsigned long sessions = 0;
unsigned long recordsPulled = 0;

void printPiAddress() {
  for (int i = 0; i < 6; i++) {
    if (piMac[i] < 0x10) Serial.print('0');
    Serial.print(piMac[i], HEX);
    if (i < 5) Serial.print(':');
  }
  Serial.print(" channel ");
  Serial.print(PI_BT_RFCOMM_CHANNEL);
}

// One connection attempt per poll. Logs only the up/down transitions, so a steady link stays
// quiet and a lost one says so once.
bool connectToPi() {
  bool ok = SerialBT.connect(piMac, PI_BT_RFCOMM_CHANNEL);
  if (ok && !linkUp) {
    Serial.print("[bt] CONNECTED to the Pi (");
    printPiAddress();
    Serial.println(")");
  } else if (!ok && (linkUp || !linkEverTried)) {
    Serial.print("[bt] Can't reach the Pi (");
    printPiAddress();
    Serial.println(") -- is its Bluetooth sender running, and are the two paired?");
  }
  linkUp = ok;
  linkEverTried = true;
  if (ok) {
    lastAnsweredAt = millis();
    sessions++;
  }
  return ok;
}

// Reads one newline-delimited line, with an *idle* timeout (resets every time a byte
// arrives) rather than a flat deadline -- a slow-but-still-streaming response shouldn't
// time out mid-line. Returns false (line left however-far it got) if nothing arrives for
// timeoutMs.
bool readLine(String& out, unsigned long timeoutMs) {
  out = "";
  // Elapsed-since-last-byte via unsigned subtraction, not an absolute deadline -- the
  // same millis()-rollover-safe pattern used throughout this firmware (see
  // wifi_client.cpp/panic_button.cpp). An absolute `millis() + timeoutMs` deadline breaks
  // once every ~49 days when millis() wraps.
  unsigned long lastByteAt = millis();
  while (millis() - lastByteAt < timeoutMs) {
    while (SerialBT.available()) {
      char c = SerialBT.read();
      if (c == '\n') return true;
      if (c != '\r') out += c;
      lastByteAt = millis();
    }
  }
  return false;
}

// Parses one of sender/protocol.py's compact JSON lines (alerts/serialization.py's
// to_json()) into a PiRecord. Returns false on malformed JSON or an unrecognized `kind`
// -- deliberately not guessed at, see alerts/serialization.py's own from_json(), which
// raises rather than defaulting on an unknown kind.
bool parseRecordLine(const String& line, PiRecord& record) {
  JsonDocument doc;
  if (deserializeJson(doc, line) != DeserializationError::Ok) return false;

  record.id = doc["id"].as<String>();
  String kind = doc["kind"].as<String>();

  if (kind == "drowsiness") {
    record.kind = PiRecordKind::DROWSINESS;
    JsonObject payload = doc["payload"];
    record.className = payload["class_name"].as<String>();
    record.severity = payload["severity"] | "";
    record.reason = payload["reason"] | "";
    JsonArray probabilities = payload["probabilities"];
    if (probabilities.size() == 2) {
      record.pNotDrowsy = probabilities[0].as<float>();
      record.pDrowsy = probabilities[1].as<float>();
    }
    return true;
  }
  if (kind == "route_status") {
    record.kind = PiRecordKind::ROUTE_STATUS;
    return true;
  }
  return false;
}

}  // namespace

void bluetoothClientBegin() {
  SerialBT.begin("ESP32-Argus", /*isMaster=*/true);
  // The MAC to `trust` (and optionally allowlist as ESP32_BT_MAC) on the Pi.
  Serial.print("[bt] This ESP32's Bluetooth MAC: ");
  Serial.println(SerialBT.getBtAddressString());
}

ConfigLineResult bluetoothFetchConfig(DeviceCredentials& out) {
  // The Pi only accepts a paired, encrypted link (cv-argus BLUETOOTH_SECURITY=medium), since
  // this reply carries the device key. Pairing is SSP "Just Works": automatic on this side,
  // pre-approved on the Pi with bluetoothctl (see src/cv-argus/README.md).
  if (!connectToPi()) {
    return ConfigLineResult::Malformed;
  }
  SerialBT.print("CONFIG\n");
  String line;
  ConfigLineResult result = ConfigLineResult::Malformed;
  if (readLine(line, PI_BT_LINE_TIMEOUT_MS)) {
    result = parseConfigLine(line, out);
    if (result == ConfigLineResult::Malformed) {
      Serial.println("[bt] CONFIG: malformed reply from the Pi");
    }
  } else {
    Serial.println("[bt] CONFIG: timed out waiting for the Pi");
  }
  SerialBT.disconnect();
  return result;
}

int bluetoothPullAlerts(bool (*onRecord)(const PiRecord& record)) {
  if (!connectToPi()) {
    return -1;
  }

  SerialBT.print("PULL ");
  SerialBT.print(PI_BT_PULL_COUNT);
  SerialBT.print("\n");

  String ackIds;
  int pulled = 0;
  bool protocolOk = true;

  while (true) {
    String line;
    if (!readLine(line, PI_BT_LINE_TIMEOUT_MS)) {
      Serial.println("[bt] Timed out waiting for a line from the Pi");
      protocolOk = false;
      break;
    }
    if (line == "END") break;

    pulled++;
    recordsPulled++;
    PiRecord record;
    if (parseRecordLine(line, record) && onRecord(record)) {
      if (ackIds.length() > 0) ackIds += ",";
      ackIds += record.id;
    }
    // Anything not added to ackIds above (unparseable line, or onRecord returned false)
    // is left unacked on purpose -- see this function's own doc comment.
  }

  if (protocolOk && ackIds.length() > 0) {
    SerialBT.print("ACK ");
    SerialBT.print(ackIds);
    SerialBT.print("\n");

    String ackedLine;
    if (!readLine(ackedLine, PI_BT_LINE_TIMEOUT_MS) || !ackedLine.startsWith("ACKED")) {
      Serial.println("[bt] Did not get a well-formed ACKED response");
      protocolOk = false;
    }
  }

  SerialBT.disconnect();
  return protocolOk ? pulled : -1;
}

bool bluetoothLinkUp() { return linkUp; }

void bluetoothLogStatus() {
  Serial.print("[bt] Pi link ");
  Serial.print(linkUp ? "UP" : "DOWN");
  Serial.print(" (");
  printPiAddress();
  Serial.print("): ");
  Serial.print(sessions);
  Serial.print(" session(s), ");
  Serial.print(recordsPulled);
  Serial.print(" record(s) since boot");
  if (sessions > 0) {
    Serial.print(", last answer ");
    Serial.print((millis() - lastAnsweredAt) / 1000.0, 1);
    Serial.print(" s ago");
  }
  Serial.println();
}
