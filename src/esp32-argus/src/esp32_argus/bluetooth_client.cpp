#include "bluetooth_client.h"

#include <BluetoothSerial.h>

#include <utility>
#include <vector>

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
  Serial.print(PI_BT_CHANNEL);
}

// One connection attempt per poll. Logs only the up/down transitions, so a steady link stays
// quiet and a lost one says so once.
bool connectToPi() {
  bool ok = SerialBT.connect(piMac, PI_BT_CHANNEL);
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
  // One allocation per line up front. Growing a String one char at a time reallocates on almost
  // every byte, which fragments the heap the Bluetooth stack already crowds, until an allocation
  // fails (see bluetoothExchange).
  out.reserve(PI_BT_LINE_RESERVE);
  // Elapsed-since-last-byte via unsigned subtraction, not an absolute deadline -- the
  // same millis()-rollover-safe pattern used throughout this firmware (see
  // wifi_client.cpp). An absolute `millis() + timeoutMs` deadline breaks
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

}  // namespace

void bluetoothClientBegin() {
  SerialBT.begin("ESP32-Argus", /*isMaster=*/true);
  // This is the value the Pi needs in its .env (ESP32_BT_MAC): its pairing agent accepts only
  // this address. Not the MAC esptool or the upload log prints -- that's the WiFi base MAC.
  String mac = SerialBT.getBtAddressString();
  mac.toUpperCase();
  Serial.print("[bt] This ESP32's Bluetooth MAC: ");
  Serial.print(mac);
  Serial.print(" (set ESP32_BT_MAC=");
  Serial.print(mac);
  Serial.println(" on the Pi)");
}

ConfigLineResult bluetoothFetchConfig(DeviceCredentials& out) {
  // The Pi only accepts a paired, encrypted link (cv-argus BLUETOOTH_SECURITY=medium), since
  // this reply carries the device key. Pairing is SSP "Just Works": automatic on this side, and
  // on the Pi cv-argus's agent accepts only ESP32_BT_MAC (sender/bluez_profile.py).
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

namespace {

// "END" or "END <n>": true, with *n set (0 when the Pi sent no count).
bool parseEnd(const String& line, int* n) {
  if (line == "END") {
    *n = 0;
    return true;
  }
  if (!line.startsWith("END ")) return false;
  *n = line.substring(4).toInt();
  return true;
}

}  // namespace

int bluetoothExchange(const char* request, RecordReport (*onRecord)(const PiRecord& record),
                      int* dlqWaiting) {
  *dlqWaiting = 0;
  if (!connectToPi()) {
    return -1;
  }

  SerialBT.print(request);
  SerialBT.print("\n");

  // Read the whole batch before handling any of it. BluetoothSerial's receive queue is 512 bytes
  // (core 3.3.12) and silently drops what doesn't fit, and one record is several hundred bytes:
  // handling each record (logs, GPS, a backend request) while the Pi was still streaming the rest
  // lost bytes, so later lines arrived cut ("Unreadable record with no id") or never ended
  // (timeouts). A batch cut short is now not handled at all: the Pi serves it again.
  std::vector<String> lines;
  lines.reserve(PI_BT_PULL_COUNT + 1);
  bool protocolOk = true;
  while (true) {
    String line;
    if (!readLine(line, PI_BT_LINE_TIMEOUT_MS)) {
      Serial.println("[bt] Timed out waiting for a line from the Pi");
      protocolOk = false;
      break;
    }
    // A String whose allocation failed is "invalid": c_str() is NULL, and turning that into a
    // std::string throws and aborts -- the reboot loop of 2026-10-21. End the session instead; the
    // Pi serves the same batch again.
    if (line.c_str() == nullptr) {
      Serial.print("[bt] Out of memory reading the Pi's batch (free heap ");
      Serial.print(ESP.getFreeHeap());
      Serial.print(" B, largest block ");
      Serial.print(ESP.getMaxAllocHeap());
      Serial.println(" B) -- the Pi will serve it again");
      protocolOk = false;
      break;
    }
    if (parseEnd(line, dlqWaiting)) break;
    lines.push_back(std::move(line));
  }

  std::vector<std::string> acks;
  std::vector<std::string> failLines;
  int received = 0;
  // The Pi waits PI_BT_REPORT_WAIT_MS for the report after END, and each backend request over the
  // SIM800L takes seconds. Five replays took ~60 s on the real device, the Pi gave up first, and
  // the same five were replayed forever. So stop starting new records after the budget: the rest
  // are left on the Pi as they were (not reported) and come back on the next poll.
  unsigned long handlingStartedAt = millis();
  int leftForLater = 0;

  for (const String& line : lines) {
    if (!protocolOk) break;
    received++;
    recordsPulled++;
    if (millis() - handlingStartedAt >= PI_BT_HANDLE_BUDGET_MS) {
      leftForLater++;
      continue;
    }
    PiRecord record;
    if (parsePiRecord(line.c_str(), record) == ParseOutcome::Drop) {
      if (record.id.empty()) {
        Serial.println("[bt] Unreadable record with no id -- left on the Pi");
      } else {
        Serial.print("[bt] Unreadable record ");
        Serial.print(record.id.c_str());
        Serial.println(" -- ACKed away so it can't block the Pi's queue");
        acks.push_back(record.id);
      }
      continue;
    }
    RecordReport report = onRecord(record);
    if (report.kind == ReportKind::Ack) {
      acks.push_back(record.id);
    } else if (report.kind == ReportKind::Fail) {
      std::string fail = "FAIL " + record.id + " " + report.reason + " ";
      fail += report.body.empty() ? "-" : report.body;
      failLines.push_back(fail);
    }
    // Skip: not reported, the Pi keeps it as it was.
  }
  if (leftForLater > 0) {
    Serial.print("[bt] ");
    Serial.print(leftForLater);
    Serial.println(" record(s) left on the Pi for the next poll (time budget, so the Pi gets the "
                   "report before it stops waiting)");
  }

  if (protocolOk && received > 0) {
    for (const std::string& fail : failLines) {
      SerialBT.print(fail.c_str());
      SerialBT.print("\n");
    }
    if (!acks.empty()) {
      SerialBT.print("ACK ");
      for (size_t i = 0; i < acks.size(); i++) {
        if (i > 0) SerialBT.print(",");
        SerialBT.print(acks[i].c_str());
      }
      SerialBT.print("\n");
      String acked;
      if (!readLine(acked, PI_BT_LINE_TIMEOUT_MS) || !acked.startsWith("ACKED")) {
        Serial.println("[bt] Did not get a well-formed ACKED response");
        protocolOk = false;
      }
    }
    if (protocolOk) {
      SerialBT.print("DONE\n");
      String result;
      if (readLine(result, PI_BT_LINE_TIMEOUT_MS) && result.startsWith("OK")) {
        if (!failLines.empty()) {
          Serial.print("[bt] Pi: ");
          Serial.println(result);  // e.g. "OK acked=3 failed=1 dlq=7"
        }
      } else {
        Serial.println("[bt] The Pi didn't confirm the report (older cv-argus?)");
      }
    }
  }

  SerialBT.disconnect();
  return protocolOk ? received : -1;
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
  // A slow leak or fragmentation shows up here long before an allocation fails.
  Serial.print(" | free heap ");
  Serial.print(ESP.getFreeHeap());
  Serial.print(" B, largest block ");
  Serial.print(ESP.getMaxAllocHeap());
  Serial.println(" B");
}
