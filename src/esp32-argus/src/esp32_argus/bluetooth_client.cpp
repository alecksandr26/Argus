#include "bluetooth_client.h"

#include <BluetoothSerial.h>
#include <freertos/FreeRTOS.h>
#include <freertos/stream_buffer.h>

#include <new>

#include "config.h"

namespace {
BluetoothSerial SerialBT;
uint8_t piMac[6] = PI_BT_MAC_ADDRESS;

// Link bookkeeping, for the Serial log only.
bool linkUp = false;
bool linkEverTried = false;
unsigned long lastConnectedAt = 0;
unsigned long lastSessionOkAt = 0;
unsigned long sessions = 0;
unsigned long sessionsOk = 0;
unsigned long recordsPulled = 0;
int connectFailsInARow = 0;
// Why sessions failed since boot (the status line shows them).
struct Failures {
  unsigned long connect = 0, lineTimeout = 0, badEnd = 0, tooMany = 0, oom = 0, noReply = 0,
                badReply = 0, btResets = 0;
} fails;

// Our own receive buffer (PI_BT_RX_BUFFER), fed by BluetoothSerial::onData() from the Bluetooth
// task: the core's own queue is 512 B and silently drops whatever doesn't fit, so a backed-up
// batch never reached END. Null if it couldn't be allocated (then the core's queue is used).
StreamBufferHandle_t rxBuf = nullptr;
volatile unsigned long rxBytes = 0;
volatile unsigned long rxDropped = 0;
uint8_t chunk[256];
size_t chunkLen = 0;
size_t chunkPos = 0;

void onPiData(const uint8_t* data, size_t len) {
  size_t sent = xStreamBufferSend(rxBuf, data, len, 0);
  rxBytes += sent;
  if (sent < len) rxDropped += len - sent;
}

// Forgets whatever a previous (broken) session left unread, so it can't start the next one.
void drainRx() {
  chunkLen = chunkPos = 0;
  if (rxBuf) {
    xStreamBufferReset(rxBuf);
  } else {
    while (SerialBT.available()) SerialBT.read();
  }
}

// The next received byte, waiting at most `waitMs`. False if none came.
bool nextByte(char& c, uint32_t waitMs) {
  if (!rxBuf) {
    unsigned long start = millis();
    while (!SerialBT.available()) {
      if (millis() - start >= waitMs) return false;
      vTaskDelay(1);
    }
    c = (char)SerialBT.read();
    return true;
  }
  if (chunkPos >= chunkLen) {
    chunkLen = xStreamBufferReceive(rxBuf, chunk, sizeof(chunk), pdMS_TO_TICKS(waitMs));
    chunkPos = 0;
    if (chunkLen == 0) return false;
  }
  c = (char)chunk[chunkPos++];
  return true;
}

void startBluetooth() {
  // Classic Bluetooth only: this firmware never uses BLE, and disableBLE makes the core start the
  // controller in classic mode and release the BLE controller's memory (btMemRelease), which
  // leaves more heap for the classic stack.
  SerialBT.begin("ESP32-Argus", /*isMaster=*/true, /*disableBLE=*/true);
}

void printPiAddress() {
  for (int i = 0; i < 6; i++) {
    if (piMac[i] < 0x10) Serial.print('0');
    Serial.print(piMac[i], HEX);
    if (i < 5) Serial.print(':');
  }
  Serial.print(" channel ");
  Serial.print(PI_BT_CHANNEL);
}

// Retry pacing while the Pi doesn't answer: 2 s, 4 s, 8 s ... up to PI_BT_RETRY_MAX_MS, back to
// every poll as soon as it answers. Each failed connect still costs the Bluetooth stack heap.
uint32_t retryDelayMs = 0;
unsigned long nextTryAt = 0;
unsigned long lastLowHeapLogAt = 0;

// The Bluetooth stack aborts (assert in fixed_queue_new) when it can't allocate during a
// connect, which reboots the board. So a connect is only attempted with heap to spare.
bool heapAllowsConnect() {
  uint32_t freeHeap = ESP.getFreeHeap();
  uint32_t block = ESP.getMaxAllocHeap();
  if (freeHeap >= PI_BT_MIN_FREE_HEAP && block >= PI_BT_MIN_HEAP_BLOCK) return true;
  if (millis() - lastLowHeapLogAt >= 10000UL) {
    lastLowHeapLogAt = millis();
    Serial.printf("[bt] Skipping the Pi connect: heap too low (free %u B, largest block %u B; "
                  "need %u / %u) -- retrying shortly\n",
                  (unsigned)freeHeap, (unsigned)block, (unsigned)PI_BT_MIN_FREE_HEAP,
                  (unsigned)PI_BT_MIN_HEAP_BLOCK);
  }
  return false;
}

// One connection attempt per session. Logs only the up/down transitions, so a steady link stays
// quiet and a lost one says so once.
bool connectToPi() {
  unsigned long now = millis();
  if (retryDelayMs > 0 && (long)(now - nextTryAt) < 0) return false;  // backing off
  if (!heapAllowsConnect()) return false;
  bool ok = SerialBT.connect(piMac, PI_BT_CHANNEL);
  if (ok) drainRx();
  if (ok && !linkUp) {
    Serial.print("[bt] CONNECTED to the Pi (");
    printPiAddress();
    Serial.println(")");
  } else if (!ok && (linkUp || !linkEverTried)) {
    Serial.print("[bt] Can't reach the Pi (");
    printPiAddress();
    Serial.println(") -- is its Bluetooth sender running, and are the two paired?");
  }
  if (ok) {
    retryDelayMs = 0;
    lastConnectedAt = millis();
    sessions++;
    connectFailsInARow = 0;
  } else {
    fails.connect++;
    if (++connectFailsInARow >= PI_BT_RESET_AFTER_FAILS) {
      // The stack can get stuck after the Pi drops a session mid-way: start it again.
      Serial.printf("[bt] %d failed connects in a row: restarting the Bluetooth stack\n",
                    connectFailsInARow);
      connectFailsInARow = 0;
      fails.btResets++;
      SerialBT.end();
      vTaskDelay(pdMS_TO_TICKS(200));
      startBluetooth();
    }
    retryDelayMs = retryDelayMs == 0 ? PI_BT_POLL_INTERVAL_MS : retryDelayMs * 2;
    if (retryDelayMs > PI_BT_RETRY_MAX_MS) retryDelayMs = PI_BT_RETRY_MAX_MS;
    nextTryAt = millis() + retryDelayMs;
    Serial.printf("[bt] Pi not answering -- next try in %u s (heap free %u B)\n",
                  (unsigned)(retryDelayMs / 1000), (unsigned)ESP.getFreeHeap());
  }
  linkUp = ok;
  linkEverTried = true;
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
      char c;
      if (!nextByte(c, 5)) continue;  // nothing yet (waits 5 ms: the other tasks run)
      if (c == '\n') return true;
      if (c == '\r') continue;
      if (out.size() >= PI_BT_LINE_MAX) {  // a runaway line: never let it eat the heap
        if (oom) *oom = true;
        return false;
      }
      out += c;
      lastByteAt = millis();
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
  rxBuf = xStreamBufferCreate(PI_BT_RX_BUFFER, 1);
  if (rxBuf) {
    SerialBT.onData(onPiData);  // the bytes come to rxBuf instead of the core's 512 B queue
  } else {
    Serial.printf("[bt] Couldn't allocate the %u B receive buffer: using the core's 512 B queue\n",
                  (unsigned)PI_BT_RX_BUFFER);
  }
  startBluetooth();
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
        fails.oom++;
        logOutOfMemory();
      } else {
        fails.lineTimeout++;
        Serial.printf("[bt] Session failed: no line from the Pi for %lu ms after %u record(s) "
                      "(\"%s\"; %lu B received, %lu B dropped since boot)\n",
                      (unsigned long)PI_BT_LINE_TIMEOUT_MS, (unsigned)lines.size(),
                      request.c_str(), (unsigned long)rxBytes, (unsigned long)rxDropped);
      }
      break;
    }
    if (line.compare(0, 3, "END") == 0) {
      if (parseEndLine(line, end)) return true;
      fails.badEnd++;
      Serial.print("[bt] Session failed: malformed END from the Pi: ");
      Serial.println(line.c_str());
      break;
    }
    if (lines.size() >= (size_t)PI_BT_PULL_COUNT) {
      fails.tooMany++;
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
      fails.noReply++;
      Serial.print("[bt] No reply to \"");
      Serial.print(s.line.substr(0, 40).c_str());
      Serial.println("\" -- lines already sent stand; the rest goes next session");
      break;
    }
    int n = 0;
    if (s.expectReply == "OK") {
      gotOk = parseOkLine(reply, summary);
      if (!gotOk) {
        fails.badReply++;
        Serial.print("[bt] Expected OK, got: ");
        Serial.println(reply.c_str());
      }
      break;  // DONE is always last
    }
    if (!parseCountReply(reply, s.expectReply.c_str(), n)) {
      fails.badReply++;
      Serial.print("[bt] Expected ");
      Serial.print(s.expectReply.c_str());
      Serial.print(", got: ");
      Serial.println(reply.c_str());
      break;
    }
  }
  SerialBT.disconnect();
  if (gotOk) {
    sessionsOk++;
    lastSessionOkAt = millis();
  }
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
  Serial.printf("%lu connect(s), %lu complete session(s), %lu record(s) since boot", sessions,
                sessionsOk, recordsPulled);
  if (sessions > 0) {
    Serial.printf(", last connect %.1f s ago", (millis() - lastConnectedAt) / 1000.0);
  }
  if (sessionsOk > 0) {
    Serial.printf(", last complete session %.1f s ago", (millis() - lastSessionOkAt) / 1000.0);
  } else {
    Serial.print(", no complete session yet");
  }
  Serial.printf(" | failures: connect %lu, line timeout %lu, bad END %lu, too many %lu, "
                "no memory %lu, no reply %lu, bad reply %lu, BT restarts %lu | rx %lu B, "
                "dropped %lu B",
                fails.connect, fails.lineTimeout, fails.badEnd, fails.tooMany, fails.oom,
                fails.noReply, fails.badReply, fails.btResets, (unsigned long)rxBytes,
                (unsigned long)rxDropped);
  // A slow leak or fragmentation shows up here long before an allocation fails.
  Serial.print(" | free heap ");
  Serial.print(ESP.getFreeHeap());
  Serial.print(" B, largest block ");
  Serial.print(ESP.getMaxAllocHeap());
  Serial.println(" B");
}
