# Edge-chain tests: cv-argus → ESP32 → backend-argus

A pytest suite that runs the whole edge-to-cloud alert path in one process, with real code at
every hop except the Bluetooth radio:

| Hop | What runs |
|---|---|
| Pi alert queue | cv-argus's real `Buffer` (SQLite, WAL) |
| Pi Bluetooth server | cv-argus's real `SenderServer` and its PULL/DLQ/FAIL/ACK/DONE protocol, over `FakeTransport` |
| ESP32 relay loop | `esp32_sim.SimulatedEsp32`, a step-for-step Python mirror of `bluetoothExchange()`, `parsePiRecord()`, `decidePiEvent()`, the compose functions and `handle()` |
| Cloud | backend-argus's real FastAPI app, authenticated with a real per-truck device key, on mongomock |

The point is the seams none of the per-module suites cover: the protocol grammar end to end,
"live first, dead-letter on failure" (only what the ESP32 couldn't deliver stays on the Pi, and
it's replayed later with its original position and time), device-key auth, and what the alerts
look like once they land in the backend. There's no grip sensor yet, so alerts follow the Pi's
own severity, like the firmware.

## Running it

From the `src/tests` venv (see `../README.md`). No Docker, no MongoDB, no TensorFlow/MediaPipe.

```sh
pip install -r src/tests/requirements.txt
cd src/tests/edge_chain && pytest
```

## What it covers

- A camera critical becomes exactly one `critical` alert (reason as `alert_type`, the model, the
  scores, GPS, `grip_status: "good"` as a placeholder); a heartbeat relays the camera's
  severity as `vigilance`; the fall back resolves the alert.
- A record the backend rejects comes back as `FAIL`, waits in the Pi's dead-letter queue, and
  the replay delivers it, with the position where it happened.
- Without a GPS fix an alert is dead-lettered (`no_gps`) and a heartbeat is skipped.

- `test_edge_chain_scenarios.py` and `test_edge_chain_edge_cases.py` drive cv-argus's real
  `Orchestrator` (no hand-built readings) through the chain: incidents, recovery, `No Face`,
  backlogs, route changes, two cameras, an ESP32 reset mid-session. Known bugs are
  `xfail(strict=True)` with their scenario ID from `docs/designs/edge-cloud-scenarios.md`; the
  newer ones also pin `raises=AssertionError`, so a crash in the harness can't pass as the bug.
- `test_firmware_vectors.py` replays the shared fusion vectors (X-15) that the C++ host test
  replays against `fusion.cpp`.

`esp32_sim.py` mirrors today's firmware. Known gaps without a test here for now (removed by
user decision 2026-10-20, still listed in `docs/designs/edge-cloud-scenarios.md`): a cell-tower
position when there's no GPS fix, and camera-offline detection. The no-face scenarios drive cv-argus's real rule
engine (`Scenario.signals`), not hand-made detections. When a firmware fix lands, mirror it here so the matching xfail starts
passing.

## What it doesn't cover

The real radio (`BluetoothSppTransport`, the ESP32's `BluetoothSerial`), the firmware's actual
C++ HTTP/JSON code, and GPS/grip hardware. The C++ fusion logic itself has its own host-compiled
tests in `src/esp32-argus/test/host/`. Keep `esp32_sim.py` in sync with the firmware by hand.
