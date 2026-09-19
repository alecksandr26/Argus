# simulator-argus

A fleet simulator for Argus: N virtual trucks that provision their own demo fleet against a real
`backend-argus` and then continuously POST realistic `Status_Route`/`Alert` records over HTTP,
so `ui-argus`'s Live Operations map and Alert Triage screen have moving trucks and fresh alerts
to look at — without any real ESP32/Raspberry Pi hardware, which don't exist yet. See
`CLAUDE.md` for why it's built this way.

## Quick start — one-command demo

The fastest way to see it work: a fully self-contained mini-stack (its own Mongo, backend, and
UI, published on alternate ports so it won't collide with anything else already running).

```bash
cd src/simulator-argus
./scripts/setup_osrm.sh   # optional, one-time: real road-following routes (see below) — skip
                          # this and trucks still work fine, just moving in straight lines
docker compose up --build
```

Then open **http://localhost:5174** and log in as `admin@argus.dev` / `changeme123`. Within a
few ticks you should see `SIM-001`..`SIM-00N` trucks moving on the Live Operations map, and at
least one alert land in the feed shortly after (the `drowsy_escalation`/`panic` profile trucks —
see "What to expect" below). If you skipped the `setup_osrm.sh` step, you'll see the `osrm`
container exit with a "file not found" error in the logs — that's expected and harmless, trucks
just fall back to straight-line movement between origin and destination instead of following
real roads.

Re-running `docker compose up` is safe — the simulator deletes its own previous `SIM-*` fleet
before creating a fresh one (`SIMULATOR_RESET=true` by default), so repeated runs during dev
don't pile up ghost trucks.

## Quick start — layered onto the existing root stack

If you already run the repo-root `docker-compose.yml` for `ui-argus`/`backend-argus` dev, add
the simulator to that same stack instead of spinning up a second one:

```bash
# from the repo root
docker compose --profile simulator up simulator-argus
```

This talks to the same `backend-argus`/`mongo` your root stack already has running — nothing
extra to publish or configure. It's behind a Compose **profile** on purpose: a plain
`docker compose up` never starts it, so nobody gets surprise fake alerts by accident.

## What to expect

Each virtual truck is randomly assigned one of three scripted profiles, weighted by
`SIMULATOR_SCENARIO_WEIGHTS` (default 45% `normal` / 35% `drowsy_escalation` / 20% `panic` — with
the default `SIMULATOR_TRUCK_COUNT` of `6` you'll typically see several of each, but it's a
weighted draw, not a guaranteed round-robin):

| Profile | Behavior |
|---|---|
| `normal` | Vigilance stays `low` with occasional `medium` blips (`SIMULATOR_MEDIUM_BLIP_PROBABILITY`). No alerts. |
| `drowsy_escalation` | Vigilance drifts `low → medium → critical` over a fraction of the route's configured duration, fires one `fusion` `Alert` at the peak (with plausible AI scores + a bad grip reading), then recovers and auto-resolves that alert. Repeats on a cycle for as long as the route runs. |
| `panic` | Vigilance stays low/medium; fires one unresolved `panic_button` `Alert` at a random point in the first third of the route, and leaves it open for a guardian to review in Alert Triage. |

Event timing scales with `SIMULATOR_ROUTE_DURATION_MINUTES`, so the same profiles tell a
coherent story whether a route takes 5 minutes or 5 hours.

## Config

All variables have dev-safe defaults — nothing below is required to get started. See
`.env.example`.

| Var | Default | Purpose |
|---|---|---|
| `BACKEND_BASE_URL` | `http://backend-argus:8000` | where the simulator sends requests |
| `SIMULATOR_ADMIN_EMAIL` / `SIMULATOR_ADMIN_PASSWORD` | `admin@argus.dev` / `changeme123` | root_admin creds used **only once**, at startup, to provision the fleet and mint each truck's own device key |
| `SIMULATOR_TRUCK_COUNT` | `6` | how many virtual trucks to run |
| `SIMULATOR_STATUS_INTERVAL_SECONDS` | `5` | status-ping cadence (faster than `ui-argus`'s 7s poll, so every poll sees fresh data) |
| `SIMULATOR_ROUTE_DURATION_MINUTES` | `20` | wall-clock length of a simulated trip — minutes for a quick demo, hours for something closer to a real shift |
| `SIMULATOR_SCENARIO_WEIGHTS` | `normal=0.45,drowsy_escalation=0.35,panic=0.20` | relative weights for the random per-truck profile draw |
| `SIMULATOR_MEDIUM_BLIP_PROBABILITY` | `0.1` | baseline chance of a `medium` vigilance blip on an otherwise-quiet tick (holds for a randomized 30s-2min dwell once triggered) |
| `SIMULATOR_RESET` | `true` | delete previously-created `SIM-*` fleet before provisioning fresh |
| `SIMULATOR_USE_OSRM` | `true` | real road-following routes instead of a straight line — see below for the one-time setup this needs to actually take effect |
| `OSRM_BASE_URL` | `http://osrm:5000` | where to query for route geometry when `SIMULATOR_USE_OSRM=true` |

## Optional: real road-following routes via OSRM

`SIMULATOR_USE_OSRM` is **on by default** — a virtual truck walks the *real* road route via a
local OSRM instance rather than a straight-line interpolation between its origin and destination
city — but this needs a one-time setup step first, since it requires a real Mexico OSM extract
(~150MB) preprocessed into a routable graph:

```bash
cd src/simulator-argus
./scripts/setup_osrm.sh   # once — downloads + preprocesses into ./osrm-data/ (gitignored)
docker compose up --build
```

(Layering onto the root stack instead: run `setup_osrm.sh` from here, then
`docker compose --profile simulator up` from the repo root — the root stack's own `osrm` service
is gated behind that same `simulator` profile, so it comes up automatically alongside
`simulator-argus`, no separate flag needed.)

If OSRM isn't running yet, or the extract hasn't been preprocessed, `osrm_client.py` logs a
warning and falls back to the straight-line route for that truck — an unpreprocessed `osrm`
container exiting with an error is expected on a fresh checkout and never blocks the rest of the
stack from starting or running. Set `SIMULATOR_USE_OSRM=false` if you don't want real routes at
all (e.g. to skip the one-time download entirely). This is a separate, simulator-only use of
OSRM for generating realistic demo tracks — it isn't the backend/frontend's own route+ETA
feature, which remains deferred future work per the root `CLAUDE.md`.

## Running tests

```bash
pip install -r requirements.txt
pytest
```

No real backend or Docker needed — `tests/` mocks the HTTP transport entirely. `docker build`
also runs this same suite as a build gate (see `Dockerfile`), matching `backend-argus`'s own
convention.

## Troubleshooting

- **Login fails at startup** (`401` from `/api/auth/login`): `SIMULATOR_ADMIN_EMAIL`/
  `SIMULATOR_ADMIN_PASSWORD` must match the backend's own `ROOT_ADMIN_EMAIL`/`ROOT_ADMIN_PASSWORD`
  — check both compose files' env if you've changed one without the other.
- **No trucks show up on the map**: confirm you're pointed at the right backend port —
  `http://localhost:5174` (standalone stack) vs `http://localhost:5173` (root stack) use
  different backends with different data.
- **Repeated runs pile up duplicate fleets**: check `SIMULATOR_RESET` wasn't set to `false`.
