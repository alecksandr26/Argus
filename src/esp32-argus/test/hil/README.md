# test/hil: bench test against the real ESP32

Your PC plays both ends of the ESP32's world, running real code at each one:

```
PC: cv-argus Buffer + SenderServer ──Bluetooth SPP──▶ ESP32 (real firmware)
                                                        │ WiFi, HTTP, X-Device-Api-Key
PC: backend-argus (real app, in memory) ◀──────────────┘
```

`esp32_hil.py` feeds the Pi buffer what cv-argus produces (`Drowsy` readings every second, a
`route_status` heartbeat every 10 s). It prints every line the ESP32 exchanges over Bluetooth and
every alert/status the ESP32 posts to the backend (the device key in the `CONFIG` reply is
masked). Then it checks five hops:

1. The ESP32 fetched its backend credentials over Bluetooth (`CONFIG`).
2. The ESP32 connected over Bluetooth and sent `PULL`.
3. The ESP32 ACKed records, so they left the Pi buffer.
4. A route status reached the backend over HTTP.
5. A fusion alert reached the backend over HTTP.

It stops as soon as the relay checks (3–5) have happened (or after `--duration`, default 120 s) and exits `0`
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
2. **WiFi for the ESP32**, set in the shell that runs `esp32_hil.py` (the **same 2.4 GHz
   network as the PC**). The ESP32 gets it over Bluetooth (`CONFIG`), like on a truck:
   ```powershell
   $env:ARGUS_WIFI_SSID = "Casa "        # exactly, trailing space included
   $env:ARGUS_WIFI_PASSWORD = "..."
   ```
3. **Firmware `config.h`**, then flash:
   - Nothing network- or backend-related goes in `config.h` anymore: `esp32_hil.py` serves the
     WiFi network, the bench backend URL (this PC's LAN IP) and the bench device key over
     Bluetooth (`CONFIG`), and the ESP32 looks its route up from the key. A new IP or WiFi
     between runs needs no reflash. No APN is served, so the ESP32 stays on WiFi.
   - Set `PI_BT_MAC_ADDRESS` to the **PC's** Bluetooth MAC, because the PC is the Pi here.
     `ipconfig /all` shows it as "Physical Address" under "Bluetooth Network Connection".
     `B0-DC-EF-34-4C-83` becomes `{ 0xB0, 0xDC, 0xEF, 0x34, 0x4C, 0x83 }`.
   - Set `PI_BT_CHANNEL` to `0`. Windows picks its own RFCOMM channel for the incoming COM port,
     so the ESP32 has to look it up; the real Pi serves on the fixed channel 4.
   - `BENCH_FAKE_GPS`: `1` to test Bluetooth/HTTP indoors without a GPS fix (nothing gets
     relayed without one), `0` to test the real NEO-6M (see "Testing the real GPS").
     **It must be `0` on a real truck.**
4. **Pair the ESP32 with Windows**: Settings → Bluetooth & devices → Add device → Bluetooth →
   **ESP32-Argus**.
5. **Create an incoming Bluetooth COM port**: Settings → Bluetooth & devices → Devices → *More
   Bluetooth settings* → **COM Ports** tab → Add… → **Incoming** → OK. Note the port number,
   e.g. `COM5`. This is the port the ESP32's `SerialBT.connect()` reaches. It's not the USB
   port (`COM3`) used for flashing.

## Run

```powershell
.venv\Scripts\activate
python src\esp32-argus\test\hil\esp32_hil.py --serial auto
```

Allow Python through the Windows Firewall (Private networks) the first time, or the ESP32's
HTTP requests never arrive. Keep the Arduino Serial Monitor (115200) open on the USB port too,
so you see the firmware's side.

Options: `--duration 300`, `--drowsy-every 1`, `--heartbeat-every 10`, `--serial COM4,COM6`.
`--serial auto` listens on every incoming Bluetooth port. That's useful because Windows
advertises all of them, and the ESP32 connects to whichever one it finds first.

## Testing the real GPS (NEO-6M)

With `BENCH_FAKE_GPS 1`, every position is the fixed bench point `20.6597, -103.3496`, which
proves nothing about the GPS. To check that the positions come from the NEO-6M:

1. Set `BENCH_FAKE_GPS 0` in `config.h` and reflash.
2. Put the GPS antenna **by a window or outside**. A cold start can take 1–15 min to get a
   fix, and indoors often never does.
3. Watch the Serial Monitor. Every 10 s it prints a status line, for example:
   `[gps] chars=48211 bad=0 sats=7 fix=yes lat=20.659812 lon=-103.349655`

   | What you see | Meaning |
   |---|---|
   | `chars=0` | No data from the NEO-6M: wiring (TX→GPIO 32, RX→GPIO 33), power, or baud |
   | `chars` growing, `bad` growing fast | Data is corrupted: check the wiring/ground |
   | `chars` growing, `sats=0..3`, `fix=no` | Working, but it can't see enough sky yet. Move it or wait |
   | `fix=yes` | Ready. Compare `lat`/`lon` with Google Maps |

4. Get where you really are: in Google Maps, right-click your spot and click the coordinates
   to copy them. Then run:
   ```powershell
   python .\esp32_hil.py --serial auto --expect-location "20.659812, -103.349655" --tolerance-m 100
   ```
   This adds a 5th check. It fails if the ESP32 sent the fake bench position, sent no position,
   or sent one farther than `--tolerance-m` from yours. It prints the farthest position as a
   Google Maps link you can open. The NEO-6M is typically accurate to 2–10 m outdoors and
   worse by a window. 100 m only catches real mistakes, like a fake or wrong position.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Check 1 fails; Serial Monitor shows `[bt] Could not connect to the Pi's Bluetooth SPP server` | Wrong `PI_BT_MAC_ADDRESS`, `PI_BT_CHANNEL` not `0`, not paired, or no *incoming* COM port (setup steps 2–4) |
| `could not open port 'COM5'` | Wrong COM number, or another program has it open |
| Serial Monitor shows `No GPS fix yet -- ... not relaying` | The NEO-6M has no fix yet (see `[gps]` lines and "Testing the real GPS"), or set `BENCH_FAKE_GPS 1` to test without it |
| Check 5 says "sent the BENCH_FAKE_GPS position" | `BENCH_FAKE_GPS` is still `1` in the flashed firmware |
| Serial Monitor shows `No mobile data or WiFi` | Wrong `ARGUS_WIFI_SSID`/`ARGUS_WIFI_PASSWORD` (exact, spaces included), or a 5 GHz-only network (the ESP32 only does 2.4 GHz) |
| Bluetooth works but checks 4–5 fail | Firewall blocking port 8000, or the ESP32 on a different network from the PC (the URL it gets over `CONFIG` is this PC's LAN IP) |
| Check 1 fails, Serial shows `[creds] Using credentials ... (NVS cache)` | An older run's credentials are cached and the `CONFIG` fetch didn't connect; same causes as a failed `PULL` |
| Backend rejects posts (401) | The ESP32 is using stale cached credentials; it re-fetches `CONFIG` on the next poll — watch for a second `CONFIG` line |
| Check 4 fails but 1–3 pass | Fusion hasn't confirmed yet (5 s debounce). Run longer, and leave the grip input (GPIO 19) unconnected so it reads "bad grip" |
