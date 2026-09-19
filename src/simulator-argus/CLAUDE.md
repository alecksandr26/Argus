# CLAUDE.md — src/simulator-argus

This file explains why `simulator-argus` is built the way it is. See `README.md` in this
directory for practical "how do I run this" instructions, and the top-level `CLAUDE.md` for how
this module fits the rest of Argus.

## What this is

A software-only fleet simulator: N "virtual trucks" that provision their own demo fleet against
a real `backend-argus` (drivers, trucks, routes) and then continuously POST realistic
`Status_Route`/`Alert` records over real HTTP, so `ui-argus`'s Live Operations map and Alert
Triage screen have moving trucks and fresh alerts to demo against — without any ESP32/Raspberry
Pi hardware, neither of which exists yet in this repo.

This isn't a new idea invented for this module — `docs/roadmap.md` (section 7, "Testing / E2E /
integration strategy") already anticipated exactly this and left it explicitly undone: "a
software-only simulator... N virtual trucks constructing real `Alert`/`Status_Route` records...
POSTing them to a live backend," even naming it `scripts/simulate_fleet.py`. This module is that
idea, built as a real, standalone module rather than a one-off script, following the same
"own Docker container under `src/`" pattern every other module in this repo already uses.

## Reused, not reinvented: backend-argus's own schemas

`client.py`'s request bodies are copied verbatim from `backend-argus`'s Pydantic schemas
(`app/schemas/status_route.py`'s `StatusRouteCreate`, `app/schemas/alert.py`'s `AlertCreate`,
etc.) — there was no new interface to design between this module and the backend, since the
backend already exists with a typed, documented contract. The one subtlety worth calling out:
`AlertCreate`'s server-side validator (`validate_source_ai_metadata`,
`app/models/alert.py`) requires `ai_metadata`/`grip_status` together when `source == "fusion"`
and rejects them entirely when `source == "panic_button"` — `scenarios.py`'s `AlertEvent`
therefore always builds one of these two complete shapes, never a partial one toggled by a flag.

## Auth: device API key per truck, root_admin only for provisioning

The simulator authenticates its *ongoing* status/alert writes with a real per-truck **device API
key** (`X-Device-Api-Key` header), minted via `POST /api/trucks/{id}/rotate-key` — exactly the
way a real ESP32 would, not an admin-JWT shortcut. `root_admin` (`SIMULATOR_ADMIN_EMAIL`/
`SIMULATOR_ADMIN_PASSWORD`) is used **only once**, at startup, to provision the fleet and mint
each truck's key (`fleet.py`'s `provision_fleet`); every subsequent `POST /api/routes/{id}/status`
and `POST /api/alerts` call from a `VirtualTruck` uses that truck's own key, never the admin
token again.

This scoping isn't just simulator discipline — it's enforced **server-side**:
`authorize_device_or_user()` (`src/backend-argus/app/auth/dependencies.py`) resolves the truck
that owns the specific route/alert being written to and bcrypt-compares the presented key
against *that truck's own* `device_api_key_hash`. A truck's key physically cannot authenticate a
write against another truck's route or alert, so each `VirtualTruck` is naturally confined to its
own route, matching how a real ESP32/backend pairing is scoped — the simulator doesn't need to
enforce this itself, it falls out of the backend's own design.

## Data: self-provisioned, not `SEED_DEMO_DATA`

`backend-argus`'s `scripts/seed_dev_data.py` (`SEED_DEMO_DATA`) already seeds a static demo
fleet, but this module deliberately does **not** rely on it — `fleet.py` provisions its own
drivers/trucks/routes via the same admin API a real operator would use
(`POST /api/drivers`/`/api/trucks`/`/api/routes`). This makes the module fully self-contained
(works against a completely empty database, no seed step required first) and keeps its fleet
visually and operationally distinct from `seed_dev_data.py`'s `ARG-*` fixtures.

Every entity this module creates is tagged with a `SIM-` prefix (`plate_number`,
`license_number`) specifically so `fleet.py`'s `reset_previous_run()` can find and delete exactly
its own fleet (routes → their alerts → trucks → drivers) before provisioning a fresh one each
run, without ever touching a real operator's data or `seed_dev_data.py`'s own fleet. This is
also why reset works at all despite Mongo ObjectIds being freshly assigned every run — identity
is tracked by the natural key (`SIM-` prefix), not by a cached id from a previous process.

## Movement model: a synthetic origin, walked as a polyline

`Route` (`src/backend-argus/app/models/route.py`) stores only `destination_coordinates` — there
is no `origin_coordinates` field anywhere in the schema. `fleet.py`'s `ROUTE_TEMPLATES` therefore
pairs each destination with its own synthetic origin coordinate (real Mexican highway city pairs,
matching the flavor `seed_dev_data.py` already uses — CDMX↔Guadalajara, Monterrey↔Saltillo,
Puebla↔Veracruz, Tijuana↔Mexicali). Neither coordinate is ever sent to the backend as its own
field — only each tick's interpolated `current_coordinates` is.

`VirtualTruckSpec.geometry` is a list of `(lat, lon)` points — a route's full path to walk, not
just its two endpoints. `virtual_truck.py`'s `interpolate_along_polyline()` walks it by
cumulative haversine distance: at fraction `f` of the route's elapsed time, it finds the point
`f * total_length` meters along the polyline, using real great-circle distances between
consecutive points (not raw lat/lon degree differences, which distort badly at Mexico's
latitudes). This one function handles both shapes the polyline can be:

- **Default (no OSRM)**: `geometry = [origin, destination]` — a straight two-point line, which
  degenerates to plain linear interpolation (no intermediate points to walk through).
- **`SIMULATOR_USE_OSRM=true`**: `geometry` is a real OSRM driving-route polyline (dozens to
  hundreds of points), so the truck visibly follows actual highways on the map instead of
  cutting a straight line across the country. See "Optional real routes: OSRM" below.

Routes run for `SIMULATOR_ROUTE_DURATION_MINUTES` (default 20, configurable from a few minutes
to several hours) rather than a hardcoded window — movement always advances by *elapsed-time
fraction* of this configured duration, never by a physically-consistent distance/speed
relationship. This is a deliberate simplification: `current_speed` in each status ping is a
plausible cosmetic value (`random.uniform(70, 100)` km/h) independent of how fast the truck is
*actually* covering ground on the dashboard, because reconciling "a 20-minute compressed demo
trip across 500 real km" with "physically honest km/h" would require either a nonsensical
reported speed or breaking the demo-friendly compression — a live dashboard cares about seeing
plausible motion and a plausible number, not unit-consistent physics.

A truck that reaches its destination (`fraction >= 1.0`) simply parks there indefinitely with
`current_speed = 0` — there's no trip-completion lifecycle (no `PUT` to mark the route
`completed`) in this first version. That's an intentional scope cut, not an oversight: the point
of this module is populating the live dashboard and alert feed, not modeling a full route
lifecycle end to end.

## Optional real routes: OSRM

`osrm_client.py`'s `fetch_route_geometry()` queries a local OSRM instance's
`/route/v1/driving` endpoint for a real road-following polyline between a route's origin and
destination, when `SIMULATOR_USE_OSRM=true` (**on by default**). Designed to fail safe
regardless: any error at all (OSRM not running, the extract not preprocessed yet, a network
hiccup, a malformed response) is caught and logged as a warning, returning the plain
`[origin, destination]` two-point fallback instead of raising — `fleet.py`'s `provision_fleet`
therefore never blocks or crashes because of OSRM being unavailable, it just silently gets
straight-line movement for that route instead. This matters because the default being *on*
doesn't remove the one-time setup cost below — on a fresh checkout with no extract preprocessed
yet, every route silently falls back to straight lines until that setup is done.

Getting real road geometry needs a full Mexico OSM extract (~150MB from Geofabrik) preprocessed
by OSRM's own `extract`/`partition`/`customize` pipeline into a routable graph — a real, one-time
cost (bandwidth + several minutes of CPU) that can't be folded into `docker compose up` itself
without either bundling a huge preprocessed dataset into the repo or turning this module's
"quick start" promise into a multi-minute wait on first run. `scripts/setup_osrm.sh` does that
preprocessing once into `./osrm-data/` (gitignored); the `osrm` service in `docker-compose.yml`
starts automatically alongside every other service in this module's own standalone stack (no
profile gate — an unpreprocessed extract just makes it exit immediately with a clear error,
which is expected and harmless since nothing else depends on it being healthy), and in the root
`docker-compose.yml` it's gated behind the same `simulator` profile as `simulator-argus` itself,
so it comes up automatically whenever the simulator is layered onto that stack. The **full
country** extract is required, not a smaller regional one, because `ROUTE_TEMPLATES` spans
long-haul corridors across the whole country (Tijuana↔Mexicali in the north, Puebla↔Veracruz in
the south-center) that a regional extract wouldn't cover.

This is a separate, simulator-only use of OSRM purely for generating realistic demo tracks — it
is **not** the backend/frontend's own route+ETA feature the root `CLAUDE.md` describes as "still
deferred." That larger, product-facing OSRM integration (querying routes for `ui-argus`'s map
and `backend-argus`'s `routes`/`routes/:id/status` resources) remains genuinely undone; this
module's private OSRM instance exists only to make virtual trucks move realistically and has no
bearing on that separate, still-open item.

## Scenario design: scripted timing, randomized assignment

`scenarios.py` gives each `VirtualTruck` one of three named profiles
(`normal`/`drowsy_escalation`/`panic`) — but *which* profile a given truck gets is a weighted
random draw (`choose_scenario_name`, weights from `SIMULATOR_SCENARIO_WEIGHTS`, default 45%/35%/
20%), not a fixed round-robin assignment, and *when* a scripted event fires within a profile is
also randomized where it makes sense (`panic`'s single alert fires at a random tick in the first
third of the route; `normal`'s medium blips are a per-tick coin flip at
`SIMULATOR_MEDIUM_BLIP_PROBABILITY` that, once triggered, holds for a randomized 30s-2min dwell
instead of reverting the very next tick — an earlier version redrew the coin flip independently
every tick, which made "medium" last only ~1 tick and read as flickering rather than a real,
observable state). What stays scripted, deliberately, is each profile's
overall *shape*: a purely random simulator (independent random severity every tick, for every
truck) produces an undifferentiated wall of severities that doesn't demo well. A `drowsy_
escalation` that visibly climbs `low → medium → critical`, fires one `fusion` alert with
plausible AI scores and a bad grip reading, then recovers and auto-resolves, tells the same story
a guardian would actually watch happen with a real driver — the randomness is in *which* trucks
get which story and *exactly when* within it, not in whether the story coheres at all.

Trigger points inside `DrowsyEscalationScenario`/`PanicScenario` are fractions of the truck's own
`total_ticks` (derived from `SIMULATOR_ROUTE_DURATION_MINUTES` ÷
`SIMULATOR_STATUS_INTERVAL_SECONDS`), not fixed tick counts — the same profile plays out
coherently whether the route takes 5 minutes or 5 hours, satisfying "parametrized, not
hardcoded" the same way the movement/OSRM design above does.

## Known limitations

- No trip lifecycle beyond "park at destination" (see "Movement model" above).
- If two simulator instances run concurrently against the same backend with `SIMULATOR_RESET`
  racing, they can transiently collide on `SIM-` natural keys (`409`-shaped failures from the
  backend's own uniqueness checks, if any) — this module assumes a single simulator instance per
  backend, matching its one intended use case (a local/demo environment).
- `reset_previous_run()` cleans up alerts/routes/trucks/drivers it can identify by the `SIM-`
  prefix, but does not (and cannot, via the public API) verify a truck's device key still
  matches what a still-running previous instance's virtual trucks hold in memory — if you restart
  the simulator while an old instance's process is somehow still alive, the old instance's status/
  alert writes will start failing 401 once its trucks are deleted. Expected and harmless (that
  old instance is meant to be replaced), just worth knowing if you see a burst of 401s in logs
  right after a restart.
- The OSRM road-snapped movement path is code-complete and unit-tested (including its fallback
  behavior), but has not been exercised against a real, preprocessed OSRM instance in this
  session — `scripts/setup_osrm.sh`'s download-and-preprocess flow was written but not actually
  run (a real ~150MB download and multi-minute CPU-bound preprocessing step), so treat the OSRM
  path as reviewed-and-tested-in-isolation, not verified end to end against real road data yet.
