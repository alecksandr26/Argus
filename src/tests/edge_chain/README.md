# Edge-chain tests: cv-argus → ESP32 → backend-argus

A pytest suite that runs the whole edge-to-cloud path in one process, with real code at every hop
except the Bluetooth radio and the ESP32's C++:

| Hop | What runs |
|---|---|
| Pi alert queue | cv-argus's real `Buffer` (SQLite, WAL): outbox, DLQ (criticals only), leases, archive |
| Pi Bluetooth server | cv-argus's real `SenderServer`, protocol v2 (`PULL`/`DLQ 1`, `END <dlq> <outbox>`, `ACK`/`TAKEN`/`FAIL`, `DONE` → `OK`), over `FakeTransport` |
| ESP32 | `esp32_sim.SimulatedEsp32`, a module-for-module Python mirror of the firmware: the record parse, the C1.5 event rule, the session decisions, `SenderPolicy` (priority, retry windows, the DLQ's one try, outcomes), the report body and the answer's handling |
| Cloud | backend-argus's real FastAPI app on mongomock: `POST /api/devices/me/report` with a real per-truck device key |

The contracts are `docs/designs/final-tweaks-contracts.md` C1 (Bluetooth v2) and C2 (the
report). The point is the seams none of the per-module suites cover: the protocol grammar end to
end, "live first, dead-letter on failure" (a critical the backend can't take within its 60 s
window waits in the Pi's DLQ with the event composed then, and is replayed with its original
position), device-key auth, by-time event routing, and what lands in the backend.

## Running it

From the `src/tests` venv (see `../README.md`). No Docker, no MongoDB, no TensorFlow/MediaPipe.

```sh
pip install -r src/tests/requirements.txt
cd src/tests/edge_chain && pytest
```

It needs cv-argus and backend-argus from the same final-tweaks generation as the firmware (v2
Pi server, report endpoint). Last run: 66 passed on a scratch merge of T1 (`7de237d`) and T4
(`b2c23a2`) with this branch, 2026-10-23, with `gdown` installed (no stand-in).

## What it covers

- `test_edge_chain.py` (hand-built Pi records): a critical becomes one alert (`delivery: live`,
  position, scores, episode, incident) and the recovery one driver OK (`driver_ok_at`; the alert
  stays open); points carry the camera's severity as `vigilance`; a critical refused through its
  live window goes to the Pi's DLQ (attempt 1, with its event) and the replay delivers it with its
  original position (`delivery: dlq`); with no network every attempt counts and the fifth archives
  it (`max_attempts`); a FAIL resent after a session broke before `OK` is counted once (C1.6
  [sync 2]); with no position the event says so and a liveness report (`points: []`) still goes;
  an event after a stop is archived `route_finished`; a medium is archived after its 30 s window;
  an event whose age the Pi can't tell is archived `too_old`.
- `test_edge_chain_scenarios.py` and `test_edge_chain_edge_cases.py` drive cv-argus's real
  `Orchestrator` (and its rule engine) on a fake clock: incident and recovery, a 30 s Bluetooth
  outage (event times from `age_s`), a poison record, a stopped route, a lost report (no
  duplicate), a failed driver OK retried, a reboot mid-incident, no face (one CRITICAL, no
  ladder), escalation (one incident, linked alerts), no credentials then a DLQ replay, a slumped
  driver who doesn't "recover", an unsynced Pi clock, two cameras, a backlog keeping its
  position, points keeping their vigilance, a route change mid-incident, an ESP32 reset after
  `END`, and the alert landing within one poll.
- `test_provisioning.py`: the dashboard's key reaches the ESP32 over `CONFIG` and authorizes its
  reports, whose answer names the active route; rotation and revocation lock the old key out.
- `test_firmware_vectors.py` replays the vectors shared with the firmware's host tests:
  `event_vectors.json` (C1.5) against `esp32_sim.classify()`, and the fusion vectors (X-15).

Scenario IDs are in `docs/designs/edge-cloud-scenarios.md`. A known bug is marked
`xfail(strict=True, raises=AssertionError)` with its ID; none is open today.

Notes on the harness:

- The route is created with `actual_departure` an hour back: events are routed by time
  (`occurred_at = received_at - age_s`) and the scenarios' fake clock runs far ahead of the real
  one, so their events are "older" than the real time that passed.
- The Pi's leases (120 s) and outbox expiry (60 s) run on the real clock, so they never lapse
  during a test; nothing here relies on them lapsing.
- `conftest.py` stubs `gdown` when it isn't installed: the rule engine's `to_detection()` imports
  cv-argus's `model` package, whose downloader imports it; the chain never downloads a model.

## What it doesn't cover

The real radio (`BluetoothSppTransport`, the ESP32's `BluetoothSerial`), the firmware's actual
C++ (its pure modules have host tests in `src/esp32-argus/test/host/`), the sender task's
pre-emption between AT steps (an attempt is atomic here), real timeouts, and GPS hardware. Keep
`esp32_sim.py` in sync with the firmware by hand.
