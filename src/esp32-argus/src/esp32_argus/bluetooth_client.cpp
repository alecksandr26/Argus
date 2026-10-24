#include "bluetooth_client.h"

#include <BluetoothSerial.h>

#include <new>

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

// One connection attempt per session. Logs only the up/down transitions, so a steady link stays
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

void writeLine(const std::string& line) {
  SerialBT.write((const uint8_t*)line.data(), line.size());
  SerialBT.write((const uint8_t*)"\n", 1);
}

// Reads one newline-delimited line, with an *idle* timeout (resets every time a byte arrives)
// rather than a flat deadline -- a slow-but-still-streaming response shouldn't time out
// mid-line. False if nothing arrives for timeoutMs, or if the line can't be stored (out of
// memory, or longer than PI_BT_LINE_MAX: `oom` set). An allocation that fails throws
// std::bad_alloc (the core builds with -fexceptions); it is caught here, so a full heap ends the
// session instead of aborting -- the reboot loop of 2026-10-21 (commit f4da937).
bool readLine(std::string& out, unsigned long timeoutMs, bool* oom = nullptr) {
  try {
    out.clear();
    // One allocation per line up front: growing one char at a time reallocates on almost every
    // byte and fragments the heap the Bluetooth stack already crowds.
    out.reserve(PI_BT_LINE_RESERVE);
    unsigned long lastByteAt = millis();
    while (millis() - lastByteAt < timeoutMs) {
      if (!SerialBT.available()) {
        vTaskDelay(1);  // nothing yet: let the sender and GPS tasks run
        continue;
      }
      while (SerialBT.available()) {
        char c = SerialBT.read();
        if (c == '\n') return true;
        if (c == '\r') continue;
        if (out.size() >= PI_BT_LINE_MAX) {  // a runaway line: never let it eat the heap
          if (oom) *oom = true;
          return false;
        }
        out += c;
        lastByteAt = millis();
      }
    }
    return false;
  } catch (const std::bad_alloc&) {
    out.clear();
    out.shrink_to_fit();
    if (oom) *oom = true;
    return false;
  }
}

void logOutOfMemory() {
  Serial.print("[bt] Out of memory reading the Pi's batch (free heap ");
  Serial.print(ESP.getFreeHeap());
  Serial.print(" B, largest block ");
  Serial.print(ESP.getMaxAllocHeap());
  Serial.println(" B) -- the Pi will serve it again");
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
  if (!connectToPi()) return ConfigLineResult::Malformed;
  writeLine("CONFIG");
  std::string line;
  ConfigLineResult result = ConfigLineResult::Malformed;
  if (readLine(line, PI_BT_LINE_TIMEOUT_MS)) {
    result = parseConfigLine(String(line.c_str()), out);
    if (result == ConfigLineResult::Malformed) {
      Serial.println("[bt] CONFIG: malformed reply from the Pi");
    }
  } else {
    Serial.println("[bt] CONFIG: timed out waiting for the Pi");
  }
  SerialBT.disconnect();
  return result;
}

bool bluetoothOpenSession(const std::string& request, std::vector<std::string>& lines,
                          EndLine& end) {
  lines.clear();
  end = EndLine();
  if (!connectToPi()) return false;
  writeLine(request);

  try {
    lines.reserve(PI_BT_PULL_COUNT);
  } catch (const std::bad_alloc&) {
    logOutOfMemory();
    SerialBT.disconnect();
    return false;
  }
  while (true) {
    std::string line;
    bool oom = false;
    if (!readLine(line, PI_BT_LINE_TIMEOUT_MS, &oom)) {
      if (oom) {
        logOutOfMemory();
      } else {
        Serial.println("[bt] Timed out waiting for a line from the Pi");
      }
      break;
    }
    if (line.compare(0, 3, "END") == 0) {
      if (parseEndLine(line, end)) return true;
      Serial.print("[bt] Malformed END from the Pi: ");
      Serial.println(line.c_str());
      break;
    }
    if (lines.size() >= (size_t)PI_BT_PULL_COUNT) {
      Serial.println("[bt] The Pi sent more records than asked for -- ending the session");
      break;
    }
    recordsPulled++;
    try {
      lines.push_back(std::move(line));
    } catch (const std::bad_alloc&) {
      logOutOfMemory();
      break;
    }
  }
  SerialBT.disconnect();
  lines.clear();
  return false;
}

bool bluetoothCloseSession(const std::vector<ScriptLine>& script, SessionSummary& summary) {
  summary = SessionSummary();
  bool gotOk = false;
  for (const ScriptLine& s : script) {
    writeLine(s.line);
    if (s.expectReply.empty()) continue;  // FAIL: no reply
    std::string reply;
    if (!readLine(reply, PI_BT_LINE_TIMEOUT_MS)) {
      Serial.print("[bt] No reply to \"");
      Serial.print(s.line.substr(0, 40).c_str());
      Serial.println("\" -- lines already sent stand; the rest goes next session");
      break;
    }
    int n = 0;
    if (s.expectReply == "OK") {
      gotOk = parseOkLine(reply, summary);
      if (!gotOk) {
        Serial.print("[bt] Expected OK, got: ");
        Serial.println(reply.c_str());
      }
      break;  // DONE is always last
    }
    if (!parseCountReply(reply, s.expectReply.c_str(), n)) {
      Serial.print("[bt] Expected ");
      Serial.print(s.expectReply.c_str());
      Serial.print(", got: ");
      Serial.println(reply.c_str());
      break;
    }
  }
  SerialBT.disconnect();
  return gotOk;
}

void bluetoothDisconnect() {
  if (SerialBT.connected()) SerialBT.disconnect();
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
