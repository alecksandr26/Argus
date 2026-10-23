# test/hil: bench test against the real ESP32

Your PC plays both ends of the ESP32's world, running real code at each one:

```
PC: cv-argus Buffer + SenderServer ──Bluetooth SPP──▶ ESP32 (real firmware)
                                                        │ WiFi, HTTP, X-Device-Api-Key
PC: backend-argus (real app, in memory) ◀──────────────┘
```

`esp32_hil.py` feeds the Pi buffer what cv-argus's orchestrator produces under Bluetooth
protocol v2 (`docs/designs/final-tweaks-contracts.md` C1): a keepalive every 2 s, one CRITICAL
raise `--alert-after` seconds (default 15) after the ESP32's first `PULL`, keepalives while it is
held, and the recovery (a driver OK, same episode) `--recover-after` seconds (default 20) later.
There is no heartbeat and no panic button any more. It prints every line the ESP32 exchanges over
Bluetooth (the device key in the `CONFIG` reply is masked) and what reaches the backend through
`POST /api/devices/me/report`. Then it checks seven hops:

1. The ESP32 fetched its backend credentials over Bluetooth (`CONFIG`).
2. The ESP32 connected over Bluetooth and sent `PULL`.
3. The ESP32 ACKed the keepalives and `TAKEN` the events (v2).
4. A report reached the backend (the truck's `device_last_seen_at` is set). With no position at
   all the ESP32 still sends an empty liveness report every 30 s.
5. GPS points reached the route (needs a fix, or `BENCH_FAKE_GPS 1`).
6. Exactly one CRITICAL alert exists for the incident.
7. The driver OK reached it: the alert has `driver_ok_at` (it stays open; only a guardian closes it).

It stops as soon as checks 5–7 have happened (or after `--duration`, default 150 s) and exits
`0` only if every check passed. The ESP32's Serial Monitor shows the sender's side: one
`[live]`/`[dlq]` line per attempt and a `[health]` line (heap, stacks) every 30 s.

`bench_backend.py` is the backend half on its own: the real FastAPI app on `mongomock` instead
of MongoDB, listening on `0.0.0.0:8000`. No Docker, no `mongod`. It seeds one truck/route with
**fixed** ids and a **fixed** device key, so `config.h` stays valid across runs.

**Status:** this is the v2 bench (final tweaks), and it has **not run end to end yet**: not
against the real ESP32 (the v2 firmware compiles, `../compile.sh`, but hasn't been flashed), and
not against a fake one either. What is checked: the script starts and imports cv-argus's v2
`SenderServer`/`Buffer` and backend-argus's app from the final-tweaks branches (2026-10-23, on a
local merge of T1 + T4). The same Pi server, report endpoint and a Python mirror of the firmware
run together in `src/tests/edge_chain/` (66 passed on that merge). The **v1** bench (protocol v1
and the retired per-message endpoints) passed all its checks on real hardware on 2026-10-17, over
WiFi with `BENCH_FAKE_GPS 1`, including steps 3–4 below (pairing and the incoming COM port).
Flash the v2 firmware and run this before trusting the device side of v2.

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
     Bluetooth (`CONFIG`), and every report's answer tells the ESP32 its active route. A new IP
     or WiFi between runs needs no reflash. No APN is served, so the ESP32 stays on WiFi.
   - Set `PI_BT_MAC_ADDRESS` to the **PC's** Bluetooth MAC, because the PC is the Pi here.
     `ipconfig /all` shows it as "Physical Address" under "Bluetooth Network Connection".
     `B0-DC-EF-34-4C-83` becomes `{ 0xB0, 0xDC, 0xEF, 0x34, 0x4C, 0x83 }`.
   - Set `PI_BT_CHANNEL` to `0`. Windows picks its own RFCOMM channel for the incoming COM port,
     so the ESP32 has to look it up; the real Pi serves on the fixed channel 4.
   - `BENCH_FAKE_GPS`: `1` to test indoors without a GPS fix (without one, events still go
     with "position unknown", but no GPS points do, so check 5 fails), `0` to test the real
     NEO-6M (see "Testing the real GPS"). **It must be `0` on a real truck.**
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

Options: `--duration 300`, `--alert-after 15`, `--recover-after 20`, `--serial COM4,COM6`.
`--serial auto` listens on every incoming Bluetooth port. That's useful because Windows
advertises all of them, and the ESP32 connects to whichever one it finds first.

## Testing the real GPS (NEO-6M)

With `BENCH_FAKE_GPS 1`, every position is the fixed bench point `20.6597, -103.3496`, which
proves nothing about the GPS. To check that the positions come from the NEO-6M:

1. Set `BENCH_FAKE_GPS 0` in `config.h` and reflash.
2. Put the GPS antenna **by a window or outside**. A cold start can take 1–15 min to get a
   fix, and indoors often never does.
3. Watch the Serial Monitor. Every 10 s it prints a status line, for example:
   `[gps] chars=48211 bad=0 sats=7 inView=11 heard=8 bestSnr=41 boots=1 fix=yes lat=20.659812 lon=-103.349655`

   `sats` counts satellites *used in the fix*, so it stays 0 until there is one. Use
   `inView`/`heard`/`bestSnr` (from the GSV sentences) to see whether the antenna is hearing
   anything before that. `boots` counts NEO-6M power-ups (its `u-blox ag` banner): 0 or 1 is
   normal.

   | What you see | Meaning |
   |---|---|
   | `chars=0` | No data from the NEO-6M: wiring (TX→GPIO 32, RX→GPIO 33), power, or baud |
   | `chars` growing, `bad` growing fast | Data is corrupted: check the wiring/ground |
   | `heard=0` (and `bestSnr=0`) | The antenna hears no satellites: indoors, antenna unplugged from its u.FL socket, or patch facing down. Move it outside |
   | `heard` 1–3, or `bestSnr` under ~25 | Hearing satellites, but too weakly to download their orbits. Get more open sky, move the antenna away from the SIM800L, or wait |
   | `heard` ≥ 4, `bestSnr` 30+, still `fix=no` | Good signal: it's downloading orbits (cold start, up to ~15 min). Wait |
   | `boots` keeps rising | The NEO-6M is resetting, so every cold start begins again. Usually the SIM800L's 2 A transmit bursts sagging a shared supply: give the GPS its own regulator or a big capacitor |
   | `fix=yes` | Ready. Compare `lat`/`lon` with Google Maps |

4. Get where you really are: in Google Maps, right-click your spot and click the coordinates
   to copy them. Then run:
   ```powershell
   python .\esp32_hil.py --serial auto --expect-location "20.659812, -103.349655" --tolerance-m 100
   ```
   This adds an 8th check. It fails if the ESP32 sent the fake bench position, sent no position,
   or sent one farther than `--tolerance-m` from yours. It prints the farthest position as a
   Google Maps link you can open. The NEO-6M is typically accurate to 2–10 m outdoors and
   worse by a window. 100 m only catches real mistakes, like a fake or wrong position.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Check 1 fails; Serial Monitor shows `[bt] Could not connect to the Pi's Bluetooth SPP server` | Wrong `PI_BT_MAC_ADDRESS`, `PI_BT_CHANNEL` not `0`, not paired, or no *incoming* COM port (setup steps 2–4) |
| `could not open port 'COM5'` | Wrong COM number, or another program has it open |
| Check 5 fails, Serial Monitor shows `[live] liveness ...` lines | The NEO-6M has no fix yet and no last known position is saved (see `[gps]` lines and "Testing the real GPS"), or set `BENCH_FAKE_GPS 1` to test without it |
| Check 8 says "sent the BENCH_FAKE_GPS position" | `BENCH_FAKE_GPS` is still `1` in the flashed firmware |
| Serial Monitor shows `[net] No network: ...` | Wrong `ARGUS_WIFI_SSID`/`ARGUS_WIFI_PASSWORD` (exact, spaces included), or a 5 GHz-only network (the ESP32 only does 2.4 GHz) |
| Bluetooth works but checks 4–7 fail | Firewall blocking port 8000, or the ESP32 on a different network from the PC (the URL it gets over `CONFIG` is this PC's LAN IP) |
| Check 1 fails, Serial shows `[creds] Using credentials ... (NVS cache)` | An older run's credentials are cached and the `CONFIG` fetch didn't connect; same causes as a failed `PULL` |
| Backend rejects posts (401) | The ESP32 is using stale cached credentials; it re-fetches `CONFIG` on the next poll — watch for a second `CONFIG` line |
| Check 6 fails with `rejected no_route` in the Serial Monitor | The bench route has no `actual_departure` (an old `bench_backend.py`): events are routed by time (C2.2) |
| Check 7 fails but 6 passes | Run longer (`--recover-after` + a few seconds), and watch for `[live] ok ... failed` lines |
