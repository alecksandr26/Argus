# CLAUDE.md — src/simulator-argus

This file explains why `simulator-argus` is built the way it is. See `README.md` in this
directory for how to run it, and the top-level `CLAUDE.md` for how this module fits the rest of
Argus. The frozen interface it implements is `docs/designs/final-tweaks-contracts.md` (C1.5 event
rule, C2 report, C4 constants); where this file and an older doc disagree, the contracts win.

## What this is

A software-only fleet simulator: N virtual trucks that provision their own demo fleet against a
real `backend-argus` (drivers, trucks, routes) and then report over real HTTP exactly the way a
truck's ESP32 does, so `ui-argus`'s Live operations map, incident feed and route view have moving
trucks and fresh incidents to demo against without a Raspberry Pi or ESP32.

`docs/roadmap.md` (section 7) anticipated this as "a software-only simulator ... N virtual trucks
... POSTing them to a live backend". This module is that idea, built as a standalone module with
its own container under `src/`, like every other module here.

## One device write: the combined report (C2)

Every write a virtual truck makes is `POST /api/devices/me/report`, the same endpoint the ESP32
firmware posts to. The old `POST /api/routes/{id}/status` / `POST /api/alerts` / resolve calls are
gone. A report carries:

- `points`: the GPS points read since the previous report, oldest first, the newest
  `REPORT_MAX_POINTS` (10) kept. Each point has a relative `age_s`, `vigilance`, `speed`, and
  `last_known`/`location_age_s` when the fix is lost (`report.py`'s `PointBuffer`). The points of
  a report are dropped whatever its outcome: a failed report leaves a gap in the path rather than
  being resent (plan section 1).
- `event` (at most one): an alert or a driver OK with its `event_id`, `episode`, relative
  `age_s`, position, speed, `ai_metadata` and `delivery` (`live`, or `dlq` plus
  `queued_seconds` for a dead-letter replay).

The backend matches the report to the truck's active route itself (the truck never names a route)
and answers per part (`points.status`, `event.status`/`reason`, `active_route`). `report.py`'s
`classify_response` reads that answer row by row as C2.3 says. `constants.py` holds the ESP32's
values the simulator mirrors (`REPORT_INTERVAL_MS` 5 s, `GPS_READ_INTERVAL_MS` 1 s,
`ATTEMPT_TIMEOUT_WIFI_MS` 8 s, the live retry windows, `DLQ_MAX_ATTEMPTS` 5,
`ACTIVE_ROUTE_RETRY_MS` 30 s, `REPORT_MAX_POINTS` 10). They are copies of the contract, not
tuning knobs: change them only when the contract changes.

Every event's `ai_metadata.model` is `"simulator"` (`SIMULATOR_MODEL_NAME`), so a simulated alert
is never mistaken for one the real Pi pipeline (`parts-rules-v1`) produced.

## Auth: device API key per truck, root_admin only for provisioning

Every report uses that truck's own **device API key** (`X-Device-Api-Key`), minted with
`POST /api/trucks/{id}/rotate-key`, which is exactly how a real ESP32 authenticates. root_admin
(`SIMULATOR_ADMIN_EMAIL`/`SIMULATOR_ADMIN_PASSWORD`) is used **only once**, at startup, to
provision the fleet and mint the keys (`fleet.py`'s `provision_fleet`). The backend resolves the
key to its truck and matches the report to that truck's active route, so a virtual truck can only
ever write to its own route. The simulator doesn't enforce that itself; it falls out of the
backend's design.

## Data: self-provisioned, not `SEED_DEMO_DATA`

`backend-argus`'s `SEED_DEMO_DATA` seeds a static demo fleet, but this module doesn't rely on it.
`fleet.py` provisions its own drivers/trucks/routes through the admin API, so it works against an
empty database with no seed step, and its fleet stays distinct from the seed's `ARG-*` fixtures.
Routes are created `in_progress`, which sets `actual_departure = now` (C3.1); the backend matches
a report's events to the route by that time.

Everything it creates carries a `SIM-` prefix (`plate_number`, `license_number`), so
`reset_previous_run()` can find and delete exactly its own fleet (routes, their alerts, trucks,
drivers) before provisioning a fresh one, without touching anyone else's data. Identity is the
natural key (`SIM-` prefix), not a cached id from a previous process.

## The two halves of a virtual truck

`virtual_truck.py`'s `VirtualTruck` plays both devices of a real truck:

- **The Pi:** the truck's scenario holds one severity and reason per tick, like the Pi's rule
  engine. `events.py`'s `EpisodeTracker` turns severity *changes* into events with the exact C1.5
  rule both real devices use: a medium alert when the severity rises to medium, a critical when
  it rises to critical (at most one upgrade per episode), a driver OK when it falls back to low
  from medium or critical, nothing for critical to medium or a reason change at the same severity.
  All events of one incident share an `episode` id (12 lowercase hex characters, like the Pi's).
- **The ESP32:** one GPS point per second, and one report per POST, sent at once when an event is
  pending, otherwise `SIMULATOR_REPORT_INTERVAL_S` after the previous report *ended*.

`run()` keeps the two halves in **two asyncio tasks**, like the ESP32's GPS loop and its
`senderTask`. The GPS/scenario tick reads a point every second while a report is in flight (an
attempt can take up to 8 s), so a slow or hanging backend doesn't stretch the scenario's timing
or leave extra holes in the path. `step()` runs one tick and then the sender inline, for tests.

## What the simulator mirrors from the ESP32's sender (C1.8, C2.3)

`delivery.py`'s `DeliveryQueue` is the ESP32's sending rules as plain logic:

- **Priority:** live critical > live medium > live OK > dead-letter critical. A dead-letter
  replay is only offered while no live event is pending at all, and it rides on a regular report.
- **Live retry windows:** a failed live event is retried at most once per second
  (`MIN_SEND_GAP_S`). When its window runs out (60 s critical, 30 s medium/OK), a critical moves
  to the dead-letter queue (DLQ) and a medium/OK is dropped (`retry_window`).
- **DLQ:** criticals only, served newest first, round-robin, so one row can't use up its attempts
  back to back. Every failed attempt counts, the live failure that dead-letters a row being attempt
  1. At 5 attempts the row is archived (kept, never resent).
- **Rejections** (`route_finished`, `no_route`, `too_old`, `wrong_truck`, `invalid`) end an event
  at once: archived for a critical, dropped otherwise.
- **No active route:** while the backend says the truck has none, points-only reports slow to one
  per 30 s (`ACTIVE_ROUTE_RETRY_S`); events still go at once. Before the first 200 the active route
  is *unknown*, not none, so points go at the normal cadence.
- **Give-up notices:** every event the truck gives up on (dropped, dead-lettered, archived),
  whatever path gave up on it, logs one WARNING line in the attempt-line format, tagged `[live]`
  or `[dlq]`, so nothing is given up silently. Every attempt that carries an event logs one INFO
  line (attempt, transport, status, duration, points, result).

**Two documented deviations; the simulator is not a reference for these rules:**

1. **No pre-emption of an in-flight DLQ replay.** On the ESP32 a new live event pre-empts a DLQ
   replay between AT steps, and the replay goes back to the DLQ without counting the attempt
   (plan section 1). A simulated attempt is one HTTP request with no steps to pre-empt between, so
   a replay already in flight runs to its end and its attempt counts.
2. **No pre-emption of an in-flight points-only report** (C1.8 [sync 2]). A live event that comes
   up during one waits for it to end.

In both cases a live event waits at most the 8 s attempt timeout. The simulator also doesn't need
the Pi's `TAKEN` lease: its queue lives in one process.

## When a truck stops

A truck ends only when its **route** is over, never on one stale event (`ReportOutcome.
route_finished`): `points.status` is `route_finished`, or the event is rejected `route_finished`
*and* the backend says the truck has no active route. An event rejected `route_finished` while a
route is active (for example a dead-letter replay of an event from before the route) only ends
that event. So stopping a route or completing a trip from the UI ends its truck within one report,
and the truck logs `route ... has ended (stopped or completed)`.

A route that runs past its planned arrival parks at the destination (speed 0) and keeps
reporting until someone ends it; the simulator never completes a route itself. When every truck
has ended, `__main__.run()` keeps the process alive, idle, until SIGTERM/SIGINT. Shutdown stays
the operator's call (Ctrl-C, `docker compose stop`, or on AWS the session's own stop), the same
as before the final tweaks. Restart it to get a new fleet.

## Movement model: a synthetic origin, walked as a polyline

`Route` stores only `destination_coordinates`, so `fleet.py`'s `ROUTE_TEMPLATES` pairs each
destination with a synthetic origin (real Mexican highway city pairs: CDMX↔Guadalajara,
Monterrey↔Saltillo, Puebla↔Veracruz, Tijuana↔Mexicali). Neither coordinate is sent as its own
field; only each GPS point's position is.

`VirtualTruckSpec.geometry` is a list of `(lat, lon)` points. `interpolate_along_polyline()` walks
it by cumulative haversine distance: at fraction `f` of the route's elapsed time it finds the point
`f * total_length` metres along the polyline. Without OSRM the geometry is `[origin, destination]`
(a straight line); with `SIMULATOR_USE_OSRM=true` it's a real driving-route polyline. Each point
gets up to about **8 m of jitter** (`_JITTER_METERS`), the scatter of a real GPS fix; the old 60 m
jitter made a 1 s track zig-zag across the road.

Movement advances by *elapsed-time fraction* of `SIMULATOR_ROUTE_DURATION_MINUTES`, never by a
physically consistent speed. The reported `speed` is a plausible cosmetic value (70 to 100 km/h,
0 once parked): squeezing a 500 km trip into a 20 minute demo can't also report an honest km/h,
and the dashboard needs plausible motion, not unit-consistent physics.

## Scenario design: scripted shapes, randomized assignment

`scenarios.py` gives each truck one of three profiles. *Which* profile is a weighted random draw
(`SIMULATOR_SCENARIO_WEIGHTS`, default 45/35/20), and *when* things happen inside a profile is
randomized where it makes sense. What stays scripted is each profile's *shape*: independent random
severities per tick produce a wall of noise that doesn't demo well, while a story a guardian would
actually watch happen does. Timing is a fraction of the truck's own `total_ticks` (route seconds,
one tick per GPS read), so the same profile plays out coherently on a 5 minute or a 5 hour route.

- **`normal`:** fine driving with a **per-route budget of one blip**: with probability
  `SIMULATOR_NORMAL_BLIP_CHANCE` (0.5) one `head_down` medium, 30 s to 2 min long, at a uniformly
  random time, then a driver OK. A medium keeps the route's map colour until a guardian closes the
  incident or an hour of OK points passes (C3.5), so any steady per-interval rate eventually turns
  every route medium. That's why the old per-5 s `SIMULATOR_MEDIUM_BLIP_PROBABILITY` was retired:
  it's still read, only to log a warning that it's ignored (`__main__.warn_retired_settings`), and
  deliberately not aliased, because a per-5 s rate has no per-route meaning (C4 [sync 3]).
- **`drowsy_escalation`:** `low -> medium -> critical -> low` (`eyes_closed`) on a cycle of about
  half the route. One medium alert, one critical upgrade in the same episode (the backend links it
  to the medium via `related_alert_id`), one driver OK. The cycle repeats while the route runs;
  with the default 20 min route the next cycle starts within the backend's 15 min incident window.
- **`no_face`:** the driver leaves the camera's view once, in the first third of the route, for 30
  to 90 s: one critical `no_face` alert with no medium step (the Pi's no-face rule goes straight to
  critical) and `scores: null` (no face, no model output), then a driver OK. It replaces the
  retired `panic` profile, because the panic button is gone from the hardware. `panic` in
  `SIMULATOR_SCENARIO_WEIGHTS` maps to `no_face` with a warning (`LEGACY_SCENARIO_NAMES`).

Only `normal` takes a blip: the other two tell their own story, and a medium after a critical
would only re-colour a route that is already critical. The driver's OK never closes an incident;
closing is a guardian/root_admin action in the UI (C3.6).

## Optional demo disruptions

`disruptions.py`, both off by default, at most one window of each per route:

- **Network outage** (`SIMULATOR_OUTAGE_CHANCE`): 70 to 150 s in which every attempt fails with
  `no_network` before reaching the backend (`transport=none` in the log). The ESP32 rules then
  play out for real. Points are dropped (a gap in the path), a medium/OK is given up after 30 s,
  and a critical is dead-lettered after 60 s. A `no_network` failure is instant and a DLQ replay
  rides every 5 s report, so a dead-lettered critical uses its 4 remaining attempts in about
  20 s. It is replayed ("Recovered after lost connection") **only if the network comes back
  within about 20 s of entering the DLQ**, i.e. within about 80 s of being raised; otherwise it is
  archived (`archived:max_attempts`), as on the real device. With a random window only a critical
  raised in the last ~80 s of the outage is replayed, so most random outages show an archived
  critical, not a recovered one.
- **Outage around the critical** (`SIMULATOR_OUTAGE_AROUND_CRITICAL`): for a dependable replay
  demo, the outage (still drawn with `SIMULATOR_OUTAGE_CHANCE`) is placed from 5 s before the
  scenario's first critical (`Scenario.first_critical_tick`) to 70 s after it
  (`OUTAGE_AROUND_CRITICAL_SECONDS`). The critical's 60 s live window runs out inside the outage
  and the network is back for its second DLQ attempt, well inside the ~80 s budget. `normal` has no
  critical, so its outage stays random.
- **GPS loss** (`SIMULATOR_GPS_LOSS_CHANCE`): 30 to 90 s without a fix. Points repeat the last good
  position as `last_known` (speed 0, `location_age_s` growing), and an event in that window carries
  that position with `speed: null`. Before the first fix, an event goes with `position: null`
  (position unknown) and no points.

## Optional real routes: OSRM

`osrm_client.py`'s `fetch_route_geometry()` queries a local OSRM instance's `/route/v1/driving`
endpoint for a road-following polyline when `SIMULATOR_USE_OSRM=true` (**on by default**). It fails
safe: any error (OSRM not running, extract not preprocessed, a network hiccup, a malformed
response) is logged as a warning and returns the straight `[origin, destination]` line, so
provisioning never blocks on OSRM. On a fresh checkout every route falls back to a straight line
until the one-time setup has run.

That setup needs a full Mexico OSM extract (~150 MB from Geofabrik) preprocessed by OSRM's
`extract`/`partition`/`customize` pipeline: real bandwidth and minutes of CPU that can't be folded
into `docker compose up` without bundling a huge dataset or making the first run slow.
`scripts/setup_osrm.sh` does it once into `./osrm-data/` (gitignored). The `osrm` service starts
with the rest of this module's standalone stack (an unpreprocessed extract just makes it exit),
and in the root `docker-compose.yml` it's behind the same `simulator` profile as
`simulator-argus`. The full-country extract is required because `ROUTE_TEMPLATES` spans
corridors across the country.

This is a simulator-only use of OSRM for realistic demo tracks. It is **not** the
backend/frontend's own route+ETA feature, which the root `CLAUDE.md` still lists as deferred.

## Deploying

The simulator only speaks the combined report endpoint. Deploy the backend **before** the
simulator image: against an older backend every report is a 404. On AWS (`src/infra-argus`) that
means shipping the backend Lambda before the simulator's Fargate image (plan section 6, step 1).

## Known limitations

- The simulator never completes a route; a route past its arrival parks at the destination
  until someone ends it (see "When a truck stops").
- Two simulator instances against one backend can collide on `SIM-` natural keys while one resets.
  The module assumes one instance per backend (a local or demo environment).
- After a restart, an old instance still running gets 401s once the new instance's reset deletes
  its trucks (`device key rejected (401)` warnings). Harmless; it's being replaced.
- The two pre-emption deviations above: the simulator isn't a reference for pre-empting an
  in-flight DLQ replay or points-only report.
- The OSRM path is unit-tested (including its fallback) but hasn't run against a real
  preprocessed extract: `setup_osrm.sh` was written, not run.
- Live runs so far have been against a local backend (a throwaway Mongo), not against the AWS
  deployment.
