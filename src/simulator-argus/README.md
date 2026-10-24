# simulator-argus

A fleet simulator for Argus: N virtual trucks that provision their own demo fleet against a real
`backend-argus` and then report the way a real truck does, through the one device endpoint
`POST /api/devices/me/report` (GPS points plus at most one event per report, authenticated with
each truck's own device API key). `ui-argus`'s Live operations map and incident feed get moving
trucks and fresh incidents to look at without a Raspberry Pi or ESP32. See `CLAUDE.md` for why
it's built this way.

## Quick start: one-command demo

The fastest way to see it work is a self-contained mini-stack (its own Mongo, backend and UI,
published on alternate ports so it won't collide with anything else already running).

```bash
cd src/simulator-argus
./scripts/setup_osrm.sh   # optional, one-time: real road-following routes (see below). Skip it
                          # and trucks still work, they just move in straight lines
docker compose up --build
```

Then open **http://localhost:5174** and log in as `admin@argus.dev` / `changeme123`. Within a
few seconds `SIM-001`..`SIM-00N` move on the Live operations map, and incidents land in the feed
from the `drowsy_escalation` and `no_face` trucks (see "What to expect"). If you skipped
`setup_osrm.sh`, the `osrm` container exits with a "file not found" error in the logs. That's
expected and harmless: trucks fall back to a straight line between origin and destination.

Re-running `docker compose up` is safe: the simulator deletes its own previous `SIM-*` fleet
before creating a fresh one (`SIMULATOR_RESET=true` by default).

## Quick start: layered onto the root stack

If you already run the repo-root `docker-compose.yml` for `ui-argus`/`backend-argus` dev, add the
simulator to that stack instead of starting a second one:

```bash
# from the repo root
docker compose --profile simulator up simulator-argus
```

It talks to the `backend-argus`/`mongo` your root stack already runs. It's behind a Compose
**profile** on purpose: a plain `docker compose up` never starts it, so nobody gets surprise fake
alerts.

**Deploy order.** The simulator only speaks the combined report endpoint. Against a backend that
predates it, every report is a 404 and nothing shows on the map. Deploy (or rebuild) the backend
first, then the simulator image. This holds on AWS too (`src/infra-argus`): ship the backend
Lambda before the simulator's Fargate image.

## What to expect

Each virtual truck runs one route. It reads one GPS point per second (jittered by up to about
8 m, so the path looks like a real fix, not a ruler line) and sends a report 5 s after the
previous one ended, carrying the newest 10 points. An event goes at once, with the points read so
far. Each truck is randomly given one of three profiles, weighted by `SIMULATOR_SCENARIO_WEIGHTS`
(default 45% `normal` / 35% `drowsy_escalation` / 20% `no_face`). It's a weighted draw, not a
round-robin, so with 6 trucks you'll usually but not always see each one.

| Profile | What happens |
|---|---|
| `normal` | Fine driving. With probability `SIMULATOR_NORMAL_BLIP_CHANCE` (0.5) the route gets **one** `head_down` medium blip, 30 s to 2 min long, at a random time: one medium alert, then a driver OK when it ends. A medium keeps the route's map colour until a guardian closes the incident or an hour of OK points passes, so `normal` gets one blip at most and about half of the normal routes stay fine from start to end. |
| `drowsy_escalation` | Eyes closing longer and longer: `low -> medium -> critical -> low` on a cycle scaled to the route's length. Per cycle that's one medium alert, one critical upgrade in the same episode (the backend links it to the medium) and one driver OK on recovery. With the default 20 min route the next cycle starts within 15 min, so the repeats group under the same incident unless a guardian closed it in between. |
| `no_face` | The driver leaves the camera's view once, in the first third of the route, for 30 to 90 s: one critical `no_face` alert with no medium step and no model scores (no face, no model output), then a driver OK. Replaces the retired `panic` profile: the panic button is gone from the hardware. An old `panic=` weight still works and maps to `no_face` with a warning. |

Every simulated alert carries `ai_metadata.model = "simulator"`, so it's never mistaken for one
the real Pi pipeline (`parts-rules-v1`) produced. The driver's OK never closes an incident. A
guardian or root_admin closes it in the UI.

**When a route ends.** When an operator stops the route or completes the trip, the backend answers
the truck's next report with `route_finished` and the truck stops reporting. A route that runs past
its planned arrival just parks at the destination (speed 0) and keeps reporting until someone ends
it. When every truck's route has ended, the process stays up, idle, until it gets SIGTERM
(`docker compose stop` / Ctrl-C). Restart it for a new fleet.

### Logs

Every report attempt that carries an event logs one line tagged `[live]` or `[dlq]`:

```
[live] truck=<id> event=critical/eyes_closed attempt=1 transport=http status=200 duration=0.04s points=3 result=delivered
```

`result` is `delivered` (stored, or already stored), `rejected:<reason>` (the backend refused
the event: `route_finished`, `no_route`, `too_old`, `wrong_truck`, `invalid`) or
`failed:<reason>` (`http_503`, `timeout`, `no_network`, ...), and the truck retries a failed
event under the ESP32's rules.

Every event the truck gives up on gets its own WARNING line in the same format, whatever caused
it: `result=dropped:retry_window` (a medium/OK whose 30 s window ran out), `dead_lettered:<reason>`
(a critical moved to the dead-letter queue), `archived:max_attempts` / `archived:route_finished`
(a critical the truck stops trying). Points-only reports log at DEBUG unless they fail.

## Optional demo disruptions

Both are off by default. Each is a chance per route of one window.

- **Network outage** (`SIMULATOR_OUTAGE_CHANCE`): 70 to 150 s in which every report fails with
  `no_network`, as when the truck loses mobile data. The points of those reports are dropped, so
  the path shows a gap. A pending medium or OK is given up after its 30 s window. A critical moves
  to the dead-letter queue after its 60 s window. From there it is retried once per 5 s report, 4
  more times, so it has about 20 s left. It is replayed ("Recovered after lost connection") only
  if the network is back within those 20 s, i.e. within about 80 s of the critical being raised.
  Otherwise it is archived, as on the real device. With a random outage window that's the
  exception: only a critical raised in the last ~80 s of the outage is replayed, so most outages
  show `archived:max_attempts`, not a recovered alert.
- **Outage around the critical** (`SIMULATOR_OUTAGE_AROUND_CRITICAL=true`, with
  `SIMULATOR_OUTAGE_CHANCE` above 0): for a dependable replay demo. The outage is placed from 5 s
  before the scenario's first critical to 70 s after it, so the critical is dead-lettered and then
  replayed. `normal` routes have no critical and keep a random outage. For a demo that must show
  a recovered alert, use `SIMULATOR_SCENARIO_WEIGHTS=no_face=1`, `SIMULATOR_OUTAGE_CHANCE=1` and
  `SIMULATOR_OUTAGE_AROUND_CRITICAL=true`.
- **GPS loss** (`SIMULATOR_GPS_LOSS_CHANCE`): 30 to 90 s with no fix. Points repeat the last good
  position as last-known (speed 0, `location_age_s` growing), and an event in that window carries
  that position with no speed. The UI labels it "Last known (N old)".

## Config

Every variable has a dev-safe default, so none is required. See `.env.example`.

| Var | Default | Purpose |
|---|---|---|
| `BACKEND_BASE_URL` | `http://backend-argus:8000` | where the simulator sends requests |
| `SIMULATOR_ADMIN_EMAIL` / `SIMULATOR_ADMIN_PASSWORD` | `admin@argus.dev` / `changeme123` | root_admin credentials used **only once**, at startup, to provision the fleet and mint each truck's device key |
| `SIMULATOR_TRUCK_COUNT` | `6` | how many virtual trucks to run |
| `SIMULATOR_REPORT_INTERVAL_S` | `5` | points-only report cadence, after the previous report ended (an event goes at once). The old name `SIMULATOR_STATUS_INTERVAL_SECONDS` is still accepted as an alias |
| `SIMULATOR_ROUTE_DURATION_MINUTES` | `20` | wall-clock length of a trip: minutes for a quick demo, hours for something closer to a shift |
| `SIMULATOR_SCENARIO_WEIGHTS` | `normal=0.45,drowsy_escalation=0.35,no_face=0.20` | weights of the per-truck profile draw. Unknown names are dropped; `panic` maps to `no_face` with a warning |
| `SIMULATOR_NORMAL_BLIP_CHANCE` | `0.5` | chance that a `normal` route gets its one `head_down` medium blip (0 = never) |
| `SIMULATOR_OUTAGE_CHANCE` | `0` | chance per route of one simulated network outage (see "Optional demo disruptions") |
| `SIMULATOR_OUTAGE_AROUND_CRITICAL` | `false` | place that outage around the scenario's first critical, for a dependable replay demo |
| `SIMULATOR_GPS_LOSS_CHANCE` | `0` | chance per route of one simulated GPS loss |
| `SIMULATOR_RESET` | `true` | delete the previous run's `SIM-*` fleet before provisioning a fresh one |
| `SIMULATOR_USE_OSRM` | `true` | real road-following routes instead of a straight line; needs the one-time setup below to take effect |
| `OSRM_BASE_URL` | `http://osrm:5000` | where to query route geometry when `SIMULATOR_USE_OSRM=true` |

**Retired:** `SIMULATOR_MEDIUM_BLIP_PROBABILITY` (the old per-5 s blip rate) is ignored. If it's
set, the simulator logs a warning at startup. It isn't aliased to `SIMULATOR_NORMAL_BLIP_CHANCE`
because a per-5 s rate has no per-route meaning.

## Optional: real road-following routes via OSRM

`SIMULATOR_USE_OSRM` is **on by default**: a virtual truck walks the real road route from a local
OSRM instance instead of a straight line between its origin and destination cities. It needs a
one-time setup first, because it requires a Mexico OSM extract (~150 MB) preprocessed into a
routable graph:

```bash
cd src/simulator-argus
./scripts/setup_osrm.sh   # once: downloads and preprocesses into ./osrm-data/ (gitignored)
docker compose up --build
```

To layer onto the root stack instead, run `setup_osrm.sh` from here, then
`docker compose --profile simulator up` from the repo root. The root stack's `osrm` service is
behind the same `simulator` profile, so it starts alongside `simulator-argus`.

If OSRM isn't running or the extract isn't preprocessed, `osrm_client.py` logs a warning and
falls back to the straight line for that truck. An `osrm` container exiting with an error is
expected on a fresh checkout and never blocks the rest of the stack. Set
`SIMULATOR_USE_OSRM=false` to skip it entirely. This is a simulator-only use of OSRM for
realistic demo tracks, not the backend/frontend route+ETA feature, which is still future work
(see the root `CLAUDE.md`).

## Running tests

```bash
pip install -r requirements.txt
pytest
```

No backend or Docker needed: `tests/` mocks the HTTP transport. `docker build` also runs this
suite as a build gate (see `Dockerfile`), like `backend-argus`.

## Troubleshooting

- **Login fails at startup** (`401` from `/api/auth/login`): `SIMULATOR_ADMIN_EMAIL`/
  `SIMULATOR_ADMIN_PASSWORD` must match the backend's `ROOT_ADMIN_EMAIL`/`ROOT_ADMIN_PASSWORD`.
  Check both compose files if you changed one without the other.
- **Trucks are provisioned but nothing moves, and every report logs `status=404`**: the backend
  predates the combined report endpoint. Deploy or rebuild the backend first (see "Deploy
  order").
- **No trucks on the map**: check you're on the right UI port. `http://localhost:5174`
  (standalone stack) and `http://localhost:5173` (root stack) use different backends.
- **A truck stopped reporting**: look for `route ... has ended (stopped or completed)` in the
  log. Someone stopped its route or completed the trip. That's by design.
- **`device key rejected (401)` warnings right after a restart**: an old instance's trucks were
  deleted by the new instance's reset. Harmless; the old instance is being replaced.
- **Repeated runs pile up duplicate fleets**: check `SIMULATOR_RESET` isn't `false`.
