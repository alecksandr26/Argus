# CLAUDE.md — `esp32_argus` sketch

Build/toolchain notes for compiling this sketch. For what the firmware *does* (Bluetooth
protocol v2 with the Pi, the four tasks and the sender's rules, the report endpoint and its
field mapping, GPS, the grip sensor / buzzer / SMS, the fusion design), see `../../README.md`;
this file only covers getting it to compile and flash, and what has been verified where.

## Status

- **The grip sensor, the cabin buzzer and the critical-alert SMS
  (`docs/designs/grip-alarm-contracts.md`, README section 6) are written and host-tested only
  (2026-10-23), never compiled for the ESP32.** That session had no Arduino toolchain: the
  Arduino-side files it touched (`esp32_argus.ino`, `sms_modem.cpp`, `modem.cpp`, `buzzer.cpp`,
  `grip_sensor.cpp`, `credentials.cpp`, `device_config.cpp`) were only checked with
  `g++ -fsyntax-only` against a throwaway mock of the Arduino/FreeRTOS API, which proves nothing
  about the real core. Run `../../test/compile.sh` before flashing, and expect: a new `gripTask`
  (4 KB stack, priority 2, core 1, like `gpsTask`; its stack comes from the heap the Bluetooth
  stack needs, so watch `heap free` and `grip` in the `[health]` line, and lower
  `GRIP_TASK_STACK` once its high-water mark is known), about 3.5 KB more static RAM (sizeof on
  the host: +1.6 KB in `SenderPolicy` for the two grip slots and the drop log, `SmsPolicy` 1.3 KB,
  `AlarmPolicy` 0.6 KB), no new library (the SMS is raw AT; no TinyGsm). Things the mock can't
  vouch for: `analogReadResolution()`/`analogRead()` on GPIO 34-39 with Bluetooth + WiFi on,
  `esp_random()` (from the core's `esp_system.h`), and the `.ino`'s generated prototypes (every
  type the new `.ino` functions use comes from a header, e.g. `CabinSignals` from
  `cabin_signals.h`, so they shouldn't matter). New files: `grip_policy.*`, `alarm_policy.*`,
  `buzzer.*`, `sms_policy.*`, `sms_modem.*`, `cabin_signals.*`, `episode_ring.h`;
  `grip_sensor.*` was rewritten (analog, GPIO 34/35/36/39) and `GRIP_SENSOR_PIN 19` removed.
  Nothing of it has run on hardware: the RFP602 threshold (500) and the SIM800L SMS need a bench
  run.

- **This firmware (the final tweaks: Bluetooth v2, one combined report, three tasks, no panic
  button) compiles but has not been flashed.** Last build, 2026-10-23, `../../test/compile.sh`
  (the `arduino-cli` bundled with the Windows Arduino IDE, run from WSL; core 3.3.12, Huge APP,
  `--warnings all`): 1,825,880 bytes (**58% flash**), 76,852 bytes of globals (**23% RAM**; was 25% before the heap fix: 6 outcome slots instead of 16).
  The only warning from our files is `BluetoothSerial` being marked deprecated by core 3.x
  (`bluetooth_client.cpp`; still supported until core 4.0). Not verified on hardware yet:
  protocol v2 with the real Pi, the report endpoint from the device, the GPRS and AWS/HTTPS
  paths, the 1 s GPS reads, and the heap/stack headroom of the three tasks with classic BT +
  WiFi + TLS running (watch the `[health]` lines). Flash it together with the v2 Pi image: v1
  and v2 don't talk to each other.
- **First flash (2026-10-23) rebooted in a loop**: right after a failed connect to the Pi,
  `assert failed: fixed_queue_new fixed_queue.c:52 ((ret->lock) != NULL)` inside the Bluetooth
  stack, i.e. the heap was exhausted. Fixed (not yet re-flashed): the WiFi radio stays off unless
  it's actually needed as the fallback, Bluetooth starts classic-only (BLE memory released), 6
  outcome slots instead of 16 (-7.5 KB static), a Pi connect is skipped when the heap is low, and
  an unreachable Pi is retried with a backoff. A `[health]` line now prints at boot too.
- **The previous (v1) firmware** compiled and flashed (2026-10-16, Arduino IDE 2.x on Windows,
  core 3.3.12, Huge APP: 56% flash, 19% RAM) to a real ESP32-D0WD-V3, and on 2026-10-17
  `../../test/hil/` passed on real hardware (Bluetooth pull from the PC acting as the Pi, HTTP to
  the bench backend over WiFi) with `BENCH_FAKE_GPS 1`. Its 2026-10-21 rework (whole-batch
  Bluetooth reads + `[net]`/`[modem]` diagnostics) compiled at 57% flash, 20% RAM, and its
  Bluetooth link (pairing, `CONFIG`) ran on the real Pi + ESP32 before that rework.
- `../../test/hil/` has been rewritten for v2 (keepalives, one critical, the driver OK, the
  report endpoint); its own README says what has and hasn't run. Real NEO-6M positions
  (`--expect-location`) aren't verified yet. It needs `BENCH_FAKE_GPS 1` in `config.h` indoors;
  set it back to `0` for a real truck.
- The pure modules are covered on the PC by `../../test/host/run.sh` (21 suites, `g++` only;
  the grip-alarm work added `grip_policy`, `alarm_policy`, `sms_policy` and `cabin_signals` and
  extended `device_config`, `sender_policy` and `sender_step`), and mirrored in Python by
  `src/tests/edge_chain/esp32_sim.py` (which doesn't model the grip events, the buzzer or the SMS
  yet).

## Toolchain setup (Arduino IDE 2.x)

1. **ESP32 board package**
   - Go to File → Preferences → *Additional boards manager URLs* and add
     `https://espressif.github.io/arduino-esp32/package_esp32_index.json`.
   - Go to Tools → Board → Boards Manager and install **"esp32" by Espressif Systems**.
   - Verified working version so far: **3.3.12**.
2. **Board selection**: **ESP32 Dev Module** (FQBN `esp32:esp32:esp32`).
   - It must be a **classic ESP32**. `bluetooth_client.cpp` uses `BluetoothSerial` (Bluetooth
     Classic SPP), which ESP32-S2/S3/C3 don't support.
3. **Port**: pick the board's `COMx` port. You only need it for upload; Verify works without
   a port.
4. **Libraries**: install these from Library Manager (Tools → Manage Libraries). Accept
   "Install all" if it offers dependencies.

   | Library Manager name | Author | Used by | Notes |
   |---|---|---|---|
   | `ArduinoJson` | Benoit Blanchon | `backend_messages.cpp`, `pi_record.cpp`, `device_config.cpp` | **v7+** required: the code uses v7's `JsonDocument` API |
   | `TinyGPSPlus` | Mikal Hart | `gps_module.cpp` | Not the older `TinyGPS` |

   No `TinyGSM`: `modem.cpp` drives the SIM800L with raw AT commands (only the separate
   `../Codigo_SMS_GPS/` bring-up sketch still needs it). `WiFi`, `HTTPClient`, `Preferences`
   (NVS) and `BluetoothSerial` ship with the ESP32 board package. Don't install them separately.

5. **Partition scheme**: go to Tools → Partition Scheme → **Huge APP (3MB No OTA/1MB SPIFFS)**.
   This setting is required. WiFi + Bluetooth Classic together make the app ~1.77 MB, and the
   default scheme only gives the app 1.25 MB (it reserves half of the flash for a second OTA
   slot, which this firmware never uses). Measured on core 3.3.12: the v1 build's 1,765,388 bytes
   was 134% of the default scheme vs. ~56% of Huge APP; this one is 1,825,248 bytes (58%). Cutting code can't fix this: the WiFi and BT stacks alone
   are most of that size.
6. Open `esp32_argus.ino`. Every `.h`/`.cpp` in this folder opens as a tab and compiles with it.
   (`../Codigo_SMS_GPS/` is a separate SIM800L/GPS bring-up sketch, not part of this build.)
7. Set the Pi's Bluetooth MAC (and GPIO pins, if wired differently) in `config.h` before
   flashing, and `PI_BT_CHANNEL` to the Pi's `BLUETOOTH_CHANNEL` (4; `0` for the Windows bench,
   see `../../test/hil/`). There's no WiFi network, APN, backend URL, device key, SMS phone or
   route id to set: those come from the Pi over Bluetooth (`CONFIG`) and from every report's
   answer (see `../../README.md` sections 1b and 2). Compiling doesn't need real values. The wheel
   sensors go on GPIO 34/35/36/39 (`GRIP_SENSOR_PINS`, ADC1 only: ADC2 pins stop reading once
   WiFi is on) and the buzzer on GPIO 13 (`BUZZER_PIN`).

## Errors already hit, and what they mean

| Error | Cause | Fix |
|---|---|---|
| `Missing FQBN (Fully Qualified Board Name)` | No board selected. Plugging in the ESP32 isn't enough: its USB-serial chip (CP2102/CH340) shows up as an unknown device, so the IDE can't auto-detect it as an ESP32 | Click the top board dropdown → *Select other board and port…* → **ESP32 Dev Module** + the COM port |
| `ArduinoJson.h: No such file or directory` (same pattern for `TinyGPSPlus.h`) | Library not installed | Install it from Library Manager (table above) |
| `Sketch too big` / `text section exceeds available space in board` (134%, max 1310720 bytes) | Default dual-OTA partition scheme gives the app only 1.25 MB | Tools → Partition Scheme → **Huge APP (3MB No OTA/1MB SPIFFS)** |
| `'BluetoothSerial' is deprecated: BluetoothSerial won't be supported in version 4.0.0 by default` | core 3.x's notice that Bluetooth Classic SPP leaves the default build in core 4.0 | Only a warning; stay on core 3.x (verified: 3.3.12). On core 4 the classic-BT stack would have to be enabled explicitly |
| No COM port listed | Windows USB-serial driver missing | Install the Silicon Labs **CP210x** or **CH340** driver, depending on the chip next to the board's USB connector |

## Command-line build: `../../test/compile.sh`

`test/compile.sh` builds the sketch for the real target (FQBN
`esp32:esp32:esp32:PartitionScheme=huge_app`, `--warnings all`) without opening the IDE, prints
only the warnings/errors from our own files plus the flash/RAM lines, and exits non-zero when the
build fails. It uses `arduino-cli` from `PATH`, or `$ARDUINO_CLI`. From WSL the simplest is the
Windows binary the Arduino IDE already bundles (it has the esp32 core and both libraries the IDE
installed; Windows paths are converted with `wslpath`):

```bash
ARDUINO_CLI="/mnt/c/Users/<you>/AppData/Local/Programs/Arduino IDE/resources/app/lib/backend/resources/arduino-cli.exe" \
  sh src/esp32-argus/test/compile.sh      # about 3 min the first time, faster after
```

A Linux `arduino-cli` needs the core and libraries installed once:

```bash
arduino-cli config add board_manager.additional_urls \
  https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core update-index && arduino-cli core install esp32:esp32
arduino-cli lib install ArduinoJson TinyGPSPlus
sh src/esp32-argus/test/compile.sh
```

Flashing from WSL needs USB passthrough (`usbipd`). Uploading from the Windows IDE (COM3) is
simpler.
