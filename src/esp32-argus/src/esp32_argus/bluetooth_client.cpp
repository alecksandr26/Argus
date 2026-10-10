#include "bluetooth_client.h"

#include <ArduinoJson.h>
#include <BluetoothSerial.h>

#include "config.h"

namespace {
BluetoothSerial SerialBT;
uint8_t piMac[6] = PI_BT_MAC_ADDRESS;

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
}

int bluetoothPullAlerts(bool (*onRecord)(const PiRecord& record)) {
  if (!SerialBT.connect(piMac)) {
    Serial.println("[bt] Could not connect to the Pi's Bluetooth SPP server");
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
