# CLAUDE.md — `esp32_argus` sketch

Build/toolchain notes for compiling this sketch in the Arduino IDE. For what the firmware
*does* (Bluetooth pull protocol, fusion state machine, backend field mapping, SMS policy), see
`../../README.md` — this file only covers getting it to compile and flash.

## Status

**Compiles and flashes** (2026-10-16, Arduino IDE 2.x on Windows, core 3.3.12, Huge APP
partition scheme): 56% flash, 19% RAM, uploaded to a real ESP32-D0WD-V3 board. On 2026-10-17 `../../test/hil/` passed all 4 checks on real
hardware (Bluetooth pull from the PC acting as the Pi, fusion, HTTP to the bench backend over
WiFi), with `BENCH_FAKE_GPS 1`. Real NEO-6M positions (`--expect-location`) aren't verified yet. `../../test/hil/` is the bench test
for that: the PC plays the Pi and the backend. It needs `BENCH_FAKE_GPS 1` in `config.h`
indoors. Set it back to `0` for a real truck.

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
   | `ArduinoJson` | Benoit Blanchon | `backend_api.cpp`, `bluetooth_client.cpp` | **v7+** required: the code uses v7's `JsonDocument` API |
   | `TinyGPSPlus` | Mikal Hart | `gps_module.cpp` | Not the older `TinyGPS` |

   No `TinyGSM`: `sms_module.cpp` drives the SIM800L with raw AT commands (only the separate
   `../Codigo_SMS_GPS/` bring-up sketch still needs it). `WiFi`, `HTTPClient` and
   `BluetoothSerial` ship with the ESP32 board package. Don't install
   them separately.

5. **Partition scheme**: go to Tools → Partition Scheme → **Huge APP (3MB No OTA/1MB SPIFFS)**.
   This setting is required. WiFi + Bluetooth Classic together make the app ~1.77 MB, and the
   default scheme only gives the app 1.25 MB (it reserves half of the flash for a second OTA
   slot, which this firmware never uses). Measured on core 3.3.12: 1,765,388 bytes is 134% of the
   default scheme vs. ~56% of Huge APP. Cutting code can't fix this: the WiFi and BT stacks alone
   are most of that size.
6. Open `esp32_argus.ino`. Every `.h`/`.cpp` in this folder opens as a tab and compiles with it.
   (`../Codigo_SMS_GPS/` is a separate SIM800L/GPS bring-up sketch, not part of this build.)
7. Fill in every placeholder in `config.h` (WiFi creds, backend URL, device API key, route id,
   the Pi's Bluetooth MAC, GPIO pins) before flashing. Compiling doesn't need real values.

## Errors already hit, and what they mean

| Error | Cause | Fix |
|---|---|---|
| `Missing FQBN (Fully Qualified Board Name)` | No board selected. Plugging in the ESP32 isn't enough: its USB-serial chip (CP2102/CH340) shows up as an unknown device, so the IDE can't auto-detect it as an ESP32 | Click the top board dropdown → *Select other board and port…* → **ESP32 Dev Module** + the COM port |
| `ArduinoJson.h: No such file or directory` (same pattern for `TinyGPSPlus.h`) | Library not installed | Install it from Library Manager (table above) |
| `Sketch too big` / `text section exceeds available space in board` (134%, max 1310720 bytes) | Default dual-OTA partition scheme gives the app only 1.25 MB | Tools → Partition Scheme → **Huge APP (3MB No OTA/1MB SPIFFS)** |
| No COM port listed | Windows USB-serial driver missing | Install the Silicon Labs **CP210x** or **CH340** driver, depending on the chip next to the board's USB connector |

## Command-line equivalent (`arduino-cli`)

This does the same as the IDE steps above, for checking compiles from a shell (e.g. WSL)
without the IDE:

```bash
arduino-cli config add board_manager.additional_urls \
  https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core update-index && arduino-cli core install esp32:esp32
arduino-cli lib install ArduinoJson TinyGPSPlus
arduino-cli compile --fqbn esp32:esp32:esp32:PartitionScheme=huge_app src/esp32-argus/src/esp32_argus
```

Flashing from WSL needs USB passthrough (`usbipd`). Uploading from the Windows IDE is simpler.
