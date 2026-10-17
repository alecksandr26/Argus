# Edge-chain tests: cv-argus → ESP32 → backend-argus

A pytest suite that runs the whole edge-to-cloud alert path in one process, with real code at
every hop except the Bluetooth radio:

| Hop | What runs |
|---|---|
| Pi alert queue | cv-argus's real `Buffer` (SQLite, WAL) |
| Pi Bluetooth server | cv-argus's real `SenderServer` and PULL/ACK protocol, over `FakeTransport` |
| ESP32 relay loop | `esp32_sim.SimulatedEsp32`, a step-for-step Python mirror of `bluetoothPullAlerts()` and `handlePiRecord()` |
| ESP32 drowsy + grip fusion | cv-argus's real `FusionOrchestrator`, which the firmware's `fusion.cpp` is a 1:1 port of |
| Cloud | backend-argus's real FastAPI app, authenticated with a real per-truck device key, on mongomock |

The point is the seams none of the per-module suites cover: the protocol grammar end to end,
the "only ACK what was actually relayed" invariant, device-key auth, and what fusion decisions
look like once they land in the backend.

## Running it

From the `src/tests` venv (see `../README.md`). No Docker, no MongoDB, no TensorFlow/MediaPipe.

```sh
pip install -r src/tests/requirements.txt
cd src/tests/edge_chain && pytest
```

## What it covers

- A drowsy reading with bad grip, sustained past the debounce window, becomes exactly one
  `critical` fusion alert with the right scores, grip, and GPS. A heartbeat then relays the
  *fused* severity as the route's `vigilance`, and sustained recovery sets `resolved_at`.
- A record the backend rejects stays unACKed on the Pi and is re-served and delivered on the
  next pull.
- No GPS fix means nothing is ACKed, so records wait on the Pi.
- An alert whose POST fails is still delivered on a later poll. **This found a real bug**:
  `FusionOrchestrator` confirmed the severity when it first raised the alert, so after a failed
  POST the re-served record looked "steady", nothing was re-raised, the record was ACKed, and
  the alert was lost for good. Fixed in both `fusion_contract.py` and the firmware's
  `fusion.cpp`: an alert is now re-raised every tick until `record_alert_posted()` confirms it.

## What it doesn't cover

The real radio (`BluetoothSppTransport`, the ESP32's `BluetoothSerial`), the firmware's actual
C++ HTTP/JSON code, and GPS/grip hardware. The C++ fusion logic itself has its own host-compiled
tests in `src/esp32-argus/test/host/`. Keep `esp32_sim.py` in sync with the firmware by hand.
