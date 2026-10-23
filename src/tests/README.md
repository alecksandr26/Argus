# src/tests — cross-module checks, no Docker

Everything here runs from **one Python venv** built from `requirements.txt` in this folder. No
Docker, no MongoDB, no TensorFlow/MediaPipe. Use Python 3.12:

```sh
python3.12 -m venv .venv && source .venv/bin/activate     # Windows: py -3.12 -m venv .venv; .venv\Scripts\activate
pip install -r src/tests/requirements.txt
```

| What | Kind | Needs |
|---|---|---|
| `smoke_cv_argus.py` | script | nothing else |
| `smoke_backend.py` | script | a running backend (`src/esp32-argus/test/hil/bench_backend.py` is enough) |
| `edge_chain/` | pytest suite, cv-argus → simulated ESP32 (Bluetooth v2 + the report sender) → backend, in one process | nothing else. See its README.md |

The bench test against the **real ESP32** lives with the firmware, in
`src/esp32-argus/test/hil/` (own `requirements.txt`).

Two standalone Python scripts, not pytest, not mocked — each one connects to a real,
already-running piece of Argus and prints a readable `[ OK ]`/`[FAIL]` log per check, so you can
actually watch a connection succeed instead of trusting a hermetic test suite's mocks. This is
deliberately **separate** from each module's own test suite:

- `src/backend-argus/tests/` and `src/cv-argus/tests/` are hermetic `pytest` suites (mocked
  Mongo, stub models) — they test the code in isolation and run with no services up at all.
- These two scripts do the opposite: they assume a real stack is already running and poke it
  over the network/in-process, to confirm the actual wiring works, not just the code's logic.

Each script is self-contained and can be run independently — neither depends on the other being
up.

## `smoke_backend.py` — confirms the real backend-argus HTTP contract

Hits a real, running `backend-argus` + MongoDB over HTTP: health check, login, `GET
/api/auth/me`, `GET /api/routes/active`, then a full device-API-key round trip, the exact
contract `src/esp32-argus`'s firmware uses (`docs/designs/final-tweaks-contracts.md` C2):

1. Create a throwaway truck and rotate its device key, then a driver and a route **created
   `in_progress`** (the backend sets its `actual_departure`, which the by-time event routing
   needs, C3.1 [sync 1]).
2. With that key, not a user token, `POST /api/devices/me/report`, the only device write:
   points plus a critical alert (`created`, points stored on the active route, `active_route`
   in the answer); the same report again (`duplicate`); a driver OK for the same episode.
3. `POST /api/routes/{id}/stop` with a reason, then two reports that must be refused
   `route_finished`: one whose points and event are timed now, and a late event timed *inside*
   the route but posted after the stop (a dead letter's replay, C2.2 rule 4 [sync 2]).

Cleans up everything it created, so it's safe to re-run. It needs a backend with the report
endpoint (backend-argus after the final-tweaks work).

Stdlib only. It needs *some* backend-argus reachable over HTTP. The simplest is the in-memory
bench backend, which is the real app on mongomock, with no Docker and no `mongod`:

```sh
python src/esp32-argus/test/hil/bench_backend.py   # terminal 1 (needs that folder's requirements.txt)
python src/tests/smoke_backend.py                 # terminal 2
```

Point it at a different host/port or different root_admin credentials with env vars —
`ARGUS_BACKEND_URL`, `ARGUS_ROOT_ADMIN_EMAIL`, `ARGUS_ROOT_ADMIN_PASSWORD` (see the script's own
docstring). Exits `0` if every check passes, `1` otherwise.

## `smoke_cv_argus.py` — confirms the real sender/ protocol code

cv-argus has no network API by default (`OUTPUTS=logging` just prints text), so "confirm the
connection" here means something more specific: cv-argus's one real **server** is
`sender.server.SenderServer`, the Bluetooth SPP server the ESP32 firmware connects to as a
client and polls with `PULL`/`ACK` (`src/cv-argus/src/sender/protocol.py`). Real Bluetooth
hardware/passthrough isn't available in this environment, so this script runs that real
`SenderServer` against cv-argus's own in-memory `FakeTransport`/`FakeListener` pair — the exact
stand-in cv-argus's own unit tests use — seeds one real `Alert` into a real `Buffer`, and plays
the client's side of the exchange by hand, printing every line exchanged. This proves the real
server/buffer/alerts/protocol code works end to end; it does **not** prove real Bluetooth
connectivity, which needs hardware no session has had access to.

**Needs nothing from `requirements.txt`.** `alerts/`/`buffer/`/`sender/` are pure stdlib (no numpy/mediapipe/
tensorflow/opencv in that import chain at all), so this script registers `cv_argus` as a module
pointing straight at `src/cv-argus/src/` itself (the same name mapping `setup.py`'s
`package_dir={"cv_argus": "src"}` does for a real install, just done at runtime via `importlib`
instead of setuptools) — a real `pip install -e src/cv-argus` would otherwise drag in the full
heavy dependency list for nothing, since this script never touches `model/` or `pipeline/`.

```sh
python src/tests/smoke_cv_argus.py
```

Exits `0` if every check passes, `1` otherwise.
