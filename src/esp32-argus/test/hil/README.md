# test/hil: bench test against the real ESP32

Your PC plays both ends of the ESP32's world, running real code at each one:

```
PC: cv-argus Buffer + SenderServer ──Bluetooth SPP──▶ ESP32 (real firmware)
                                                        │ WiFi, HTTP, X-Device-Api-Key
PC: backend-argus (real app, in memory) ◀──────────────┘
```

`esp32_hil.py` feeds the Pi buffer what cv-argus produces (`Drowsy` readings every second, a
`route_status` heartbeat every 10 s). It prints every line the ESP32 exchanges over Bluetooth and
every alert/status the ESP32 posts to the backend. Then it checks the four hops:

1. The ESP32 connected over Bluetooth and sent `PULL`.
2. The ESP32 ACKed records, so they left the Pi buffer.
3. A route status reached the backend over HTTP.
4. A fusion alert reached the backend over HTTP.

It stops as soon as all four have happened (or after `--duration`, default 120 s) and exits `0`
only if every check passed.

`bench_backend.py` is the backend half on its own: the real FastAPI app on `mongomock` instead
of MongoDB, listening on `0.0.0.0:8000`. No Docker, no `mongod`. It seeds one truck/route with
**fixed** ids and a **fixed** device key, so `config.h` stays valid across runs.

**Status:** the PC side is verified end to end against a fake ESP32 on a virtual serial port.
The real Windows Bluetooth + real firmware run hasn't happened yet. Steps 3–4 below (pairing and
the incoming COM port) are the untested part.

## Why Windows, not WSL

WSL2 has no access to the PC's Bluetooth radio or COM ports. Run this from a **Windows**
Python venv. On a Linux machine with BlueZ (e.g. the real Pi), use `--rfcomm` instead of
`--serial`.

## Setup (once)

1. **Python 3.12 venv on Windows** (PowerShell, from the repo root). The pinned backend
   packages have no wheels for 3.14 yet.
   ```powershell
   py install 3.12                      # if `py -0` doesn't list 3.12 yet
   py -3.12 -m venv .venv
   .venv\Scripts\activate
   pip install -r src\esp32-argus\test\hil\requirements.txt
   ```
2. **Firmware `config.h`**, then flash:
   - Set `WIFI_SSID` / `WIFI_PASSWORD` to the **same WiFi network as the PC**.
   - Copy `BACKEND_BASE_URL`, `DEVICE_API_KEY` and `ROUTE_ID` exactly as
     `python src\esp32-argus\test\hil\bench_backend.py` prints them (Ctrl+C after). Only the
     IP can change between runs.
   - Set `PI_BT_MAC_ADDRESS` to the **PC's** Bluetooth MAC, because the PC is the Pi here.
     `ipconfig /all` shows it as "Physical Address" under "Bluetooth Network Connection".
     `B0-DC-EF-34-4C-83` becomes `{ 0xB0, 0xDC, 0xEF, 0x34, 0x4C, 0x83 }`.
   - Set `BENCH_FAKE_GPS` to `1`, since indoors the GPS has no fix and nothing gets relayed
     without one. **Set it back to `0` before flashing a real truck.**
3. **Pair the ESP32 with Windows**: Settings → Bluetooth & devices → Add device → Bluetooth →
   **ESP32-Argus**.
4. **Create an incoming Bluetooth COM port**: Settings → Bluetooth & devices → Devices → *More
   Bluetooth settings* → **COM Ports** tab → Add… → **Incoming** → OK. Note the port number,
   e.g. `COM5`. This is the port the ESP32's `SerialBT.connect()` reaches. It's not the USB
   port (`COM3`) used for flashing.

## Run

```powershell
.venv\Scripts\activate
python src\esp32-argus\test\hil\esp32_hil.py --serial COM5
```

Allow Python through the Windows Firewall (Private networks) the first time, or the ESP32's
HTTP requests never arrive. Keep the Arduino Serial Monitor (115200) open on the USB port too,
so you see the firmware's side.

Options: `--duration 300`, `--drowsy-every 1`, `--heartbeat-every 10`.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Check 1 fails; Serial Monitor shows `[bt] Could not connect to the Pi's Bluetooth SPP server` | Wrong `PI_BT_MAC_ADDRESS`, not paired, or no *incoming* COM port (setup steps 2–4) |
| `could not open port 'COM5'` | Wrong COM number, or another program has it open |
| Serial Monitor shows `No GPS fix yet -- ... not relaying` | `BENCH_FAKE_GPS` is still `0` |
| Serial Monitor shows `WiFi down` | Wrong `WIFI_SSID`/`WIFI_PASSWORD`, or a 5 GHz-only network (the ESP32 only does 2.4 GHz) |
| Bluetooth works but checks 3–4 fail | Firewall blocking port 8000, ESP32 on a different network, or `BACKEND_BASE_URL` has a stale IP |
| Backend rejects posts (401/403/404) | `DEVICE_API_KEY` / `ROUTE_ID` in `config.h` don't match what `bench_backend.py` prints |
| Check 4 fails but 1–3 pass | Fusion hasn't confirmed yet (5 s debounce). Run longer, and leave the grip input (GPIO 19) unconnected so it reads "bad grip" |
