# src/tests — manual smoke-check scripts

(Also here: `edge_chain/`, an automated pytest suite for the cv-argus → ESP32 → backend path,
see its own README.md.)

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
/api/auth/me`, `GET /api/routes/active`, then a full device-API-key round trip (create a
throwaway truck → rotate its device key → create a driver + route → use that key, not a user
token, to `POST /api/routes/{id}/status` and `POST /api/alerts`) — the exact contract
`src/esp32-argus`'s firmware uses. Cleans up everything it created, so it's safe to re-run.

Zero dependencies (stdlib `urllib` only — nothing to `pip install`). The script itself never
needs Docker; it just needs *some* real backend-argus + MongoDB already reachable over HTTP —
Docker Compose is the easiest way to get both running together in one command:

```sh
docker compose up --build          # from the repo root, starts backend-argus + mongo + ui-argus
python src/tests/smoke_backend.py
```

**Without Docker** is possible but is genuinely more setup, not less — backend-argus needs a
real MongoDB to talk to either way, so skipping Docker means installing/running `mongod`
yourself plus the backend's own Python deps and server:

```sh
# a MongoDB instance running and reachable at mongodb://localhost:27017
pip install -r src/backend-argus/requirements.txt
cd src/backend-argus && uvicorn app.main:app --port 8000   # separate terminal
python src/tests/smoke_backend.py
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

**No install, no Docker.** `alerts/`/`buffer/`/`sender/` are pure stdlib (no numpy/mediapipe/
tensorflow/opencv in that import chain at all), so this script registers `cv_argus` as a module
pointing straight at `src/cv-argus/src/` itself (the same name mapping `setup.py`'s
`package_dir={"cv_argus": "src"}` does for a real install, just done at runtime via `importlib`
instead of setuptools) — a real `pip install -e src/cv-argus` would otherwise drag in the full
heavy dependency list for nothing, since this script never touches `model/` or `pipeline/`.

```sh
python src/tests/smoke_cv_argus.py
```

Exits `0` if every check passes, `1` otherwise.
