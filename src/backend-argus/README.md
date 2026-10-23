# backend-argus

FastAPI + MongoDB backend for Argus. Covers User, Truck, Driver, Route, Status_Route, and Alert,
plus login, and the incidents a guardian closes — see `CLAUDE.md` for why it's built this way
(auth design, the ER-diagram typo fixes, the geo storage format, RBAC, the incident/route-state
model, and known gaps). This file is the practical "how do I run it" reference. Exact request and
response shapes: `docs/designs/final-tweaks-contracts.md` (C2 device, C3 dashboard), or
`/docs` on a running backend.

## The API at a glance

| Who | Endpoint | What |
|---|---|---|
| Device (`X-Device-Api-Key`) | `POST /api/devices/me/report` | the one device write: up to 10 GPS points + at most one event (alert or driver OK); 200 with a result per part |
| Device | `GET /api/devices/me/active-route` | kept for old firmware; the report response carries `active_route` |
| Any user | `GET /api/live?since=&route=` | the dashboard's 5 s poll: active routes with their latest position, every open incident, the GPS track since the last poll |
| Any user | `GET /api/routes/{id}/track?max_points=` | a route's whole path (downsampled above 2000 points) |
| Any user | `GET /api/incidents`, `GET /api/incidents/{id}` | incidents: medium/critical alerts of one route grouped within 15 min |
| guardian, root_admin | `POST /api/incidents/{id}/close` `{note}` | close an incident (a note is required for a critical one) |
| root_admin, admin | `POST /api/routes/{id}/stop` `{reason}` | stop a route (it becomes `cancelled`; the device's later points/events are rejected `route_finished`) |
| — | users, trucks, drivers, routes, alerts CRUD, `/api/auth/*`, `/api/simulator` | as before (RBAC table in `CLAUDE.md`) |

**Legacy device endpoints, kept until every module has moved to the report** (contracts C2.4):
`POST /api/alerts`, `POST /api/routes/{id}/status`, device-key `PUT /api/alerts/{id}` with
`resolved_at` and `POST /api/alerts/{id}/resolve`. They follow the new model: `panic_button`
creates are 422, a medium/critical alert joins an incident, a status without `vigilance` stores
`unknown`, a device "resolve" sets `driver_ok_at` (409 on a finished route), and a user sending
`resolved_at` gets 422 (close the incident instead).

## Quick start (no Docker)

```sh
cd src/backend-argus
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # optional — every setting has a checked-in default (app/config.py)

# needs a MongoDB reachable at MONGO_URI (default mongodb://localhost:27017) — e.g.:
docker run -d -p 27017:27017 --name argus-mongo mongo:7

uvicorn app.main:app --reload
# -> http://localhost:8000/docs for the interactive OpenAPI UI
```

No install/packaging step is required beyond `pip install -r requirements.txt` — `app/` is a
plain importable package from this directory, which is also how `pytest` (below) and the
Dockerfile both find it.

## Running the tests

```sh
pytest                              # hermetic — mongomock-motor, no real Mongo, ~7s
ARGUS_BACKEND_MONGO_TESTS=1 pytest -m mongo   # opt-in — real Mongo via testcontainers, needs Docker
```

The default run needs nothing beyond `pip install -r requirements.txt` — no network, no Docker,
no running Mongo. See `tests/conftest.py` and `CLAUDE.md`'s "Current status" for what the opt-in
tier covers and why it's separate.

## Upgrading an existing database (once)

After deploying this backend over a database created by an older one, run the one-off,
idempotent migration (from this directory, with `MONGO_URI`/`MONGO_DB` pointing at it):

```sh
python -m scripts.migrate_final_tweaks --dry-run              # only count what would change
python -m scripts.migrate_final_tweaks                        # apply
python -m scripts.migrate_final_tweaks --close-ended-routes   # apply, and also close the incidents
                                                              # it creates on routes that already ended
```

It moves `resolved_at` to `driver_ok_at`, fills in route departure/arrival times, builds incidents
for historical alerts, and creates the unique (dedup) and TTL (GPS retention) indexes. Decide on
`--close-ended-routes` before the first real run: a later run has nothing left to group. **GPS
retention is destructive**: once the TTL index exists, GPS points older than 7 days are thinned
to one per route per minute. Until the migration has run, the backend still boots (it logs a
warning for each index it couldn't create, and keeps de-duplicating in code). A fresh database
needs no migration: the indexes are created on startup.

## Seeding demo data

```sh
python -m scripts.seed_dev_data
```

Loads a small demo fleet: 7 users (root_admin `admin@argus.dev`, three admin/operators
`operator@argus.dev`/`operator2@argus.dev`/`operator3@argus.dev`, three guardians
`guardian@argus.dev`/`guardian2@argus.dev`/`guardian3@argus.dev` — all password `changeme123`),
5 trucks spanning every `TruckStatus`, 5 drivers, 4 routes (one of each `RouteStatus` worth
demoing), and a couple of alerts. Trucks' device API keys are printed once, for testing
`POST /api/devices/me/report` (or the legacy device endpoints) with `X-Device-Api-Key` instead of
a user token. **This form is destructive** — it clears every seeded collection first, then reinserts
the full dataset, so re-running it always gives you the same clean demo state (but wipes
anything else in the database too, including data created through the UI).

If you'd rather **add** the missing demo records without wiping anything (safe to run
repeatedly, e.g. against a database that already has real data in it):

```sh
RESET_DEMO_DATA=false python -m scripts.seed_dev_data
```

Or skip the manual step entirely: set `SEED_DEMO_DATA=true` (see `docker-compose.yml`/
`app/config.py`) and the backend seeds this same dataset idempotently on every startup, right
after the usual root_admin bootstrap — safe to leave on permanently in a dev environment. Never
enable `SEED_DEMO_DATA` in a real deployment.

**To get every seeded user's role/email/password into a file** instead of just stdout — handy
for a manual QA pass or scripting against the seeded accounts — set `SEED_CREDENTIALS_FILE` to a
path, either form:

```sh
SEED_CREDENTIALS_FILE=./seed_credentials.txt python -m scripts.seed_dev_data
```

```sh
SEED_CREDENTIALS_FILE=./seed_credentials.txt RESET_DEMO_DATA=false python -m scripts.seed_dev_data
```

or the same variable on the `SEED_DEMO_DATA=true` startup path (`docker-compose.yml`/`.env`) —
in Docker, point it under `/app/app` (the bind-mounted directory) so the file actually lands on
the host, e.g. `SEED_CREDENTIALS_FILE=/app/app/seed_credentials.txt` shows up at
`./app/seed_credentials.txt`. This is a convenience, not a new secret: every password written is
the same fixed `changeme123` dev constant already documented above. Disabled
(empty) by default, and the output file is gitignored — never commit one.

Both `SEED_DEMO_DATA` and `SEED_CREDENTIALS_FILE` are plain env vars `docker-compose.yml` reads
with a `${VAR:-default}` fallback, so they can be set directly on the `docker compose up` command
line too, no `.env` file needed — this module's own compose file:

```sh
SEED_DEMO_DATA=true SEED_CREDENTIALS_FILE=/app/app/seed_credentials.txt docker compose up --build
# -> credentials land on the host at ./app/seed_credentials.txt
```

or the repo-root compose file (whole stack — see "Running the whole stack" below), from the
**repo root**:

```sh
SEED_DEMO_DATA=true SEED_CREDENTIALS_FILE=/app/app/seed_credentials.txt docker compose up --build
# -> credentials land on the host at ./src/backend-argus/app/seed_credentials.txt
```

## Running this module alone in Docker

```sh
docker compose up --build   # first run, or after a Dockerfile/requirements.txt change
docker compose up           # every run after that — reuses the already-built image
# -> http://localhost:8000/docs
```

Starts this service plus its own local `mongo` container. `--reload` is on by default in this
compose file (dev loop) — bind-mounts `./app` so edits apply without a rebuild, so you rarely
need `--build` again once the image exists (`docker images` will show `backend-argus`'s image).

**`--build` (and plain `docker compose build`) now re-runs the hermetic pytest suite as part of
the build itself** — `Dockerfile` is multi-stage, and the `runtime` stage genuinely depends on
the `test` stage passing (not just a comment saying so; see `CLAUDE.md`'s "Docker build test
gate" for the mechanism and how it's verified). A broken test fails `docker build` outright,
before any image is produced — the test files themselves never end up in the built image either.

## Running the whole stack (backend + Mongo + ui-argus)

From the **repo root** (not this directory):

```sh
docker compose up --build   # first run, or after a Dockerfile/requirements.txt change
docker compose up           # every run after that
# backend: http://localhost:8000/docs
# frontend: http://localhost:5173
```

This is the new root-level `docker-compose.yml` that wires all three together — see the root
`CLAUDE.md`'s "Cloud/server side" section. It's additive: this module's own `docker-compose.yml`
above still works standalone for solo-backend dev. No OSRM service in either compose file —
still deferred future work, not forgotten (see this module's `CLAUDE.md`).

## Config variables

All read from the environment (optionally via `.env` — see `.env.example`), all with checked-in
defaults so nothing here is required to boot:

| var | default | effect |
|---|---|---|
| `MONGO_URI` | `mongodb://localhost:27017` | Mongo connection string |
| `MONGO_DB` | `argus` | database name |
| `JWT_SECRET` | `dev-secret-change-me` | HS256 signing secret — **change this outside local dev** |
| `JWT_EXPIRE_MINUTES` | `480` | access-token lifetime (no refresh flow yet, see `CLAUDE.md`) |
| `CORS_ORIGINS` | `http://localhost:5173` | comma-separated list of allowed browser origins |
| `ROOT_ADMIN_EMAIL` | `admin@argus.dev` | email of the root_admin auto-created on startup if missing (see `CLAUDE.md`'s "Auth design") |
| `ROOT_ADMIN_PASSWORD` | `changeme123` | **raw** password for that account — **change this outside local dev** |
| `ROOT_ADMIN_FIRST_NAME` | `Root` | first name on the bootstrapped account |
| `ROOT_ADMIN_LAST_NAME` | `Admin` | last name on the bootstrapped account |
| `ROOT_ADMIN_PHONE_NUMBER` | `+00-000-0000` | phone number on the bootstrapped account (required field, no real use yet) |
| `SEED_DEMO_DATA` | `false` | when `true`, idempotently seeds a demo fleet (admins/guardians/trucks/drivers/routes — see "Seeding demo data" above) on every startup, right after the root_admin bootstrap. Never enable outside local dev. |
| `SIMULATOR_CONTROL` | `disabled` | `ecs` enables root_admin-only `/api/simulator` on/off control of the cloud simulator task (set by `src/infra-argus`); `disabled` makes those endpoints 503 — local dev keeps using `docker compose --profile simulator` |
| `SIMULATOR_ECS_CLUSTER` / `SIMULATOR_TASK_DEFINITION` | empty | ECS cluster name / task family to run (set by `src/infra-argus`) |
| `SIMULATOR_SUBNETS` / `SIMULATOR_SECURITY_GROUPS` | empty | comma-separated, for the task's network config (set by `src/infra-argus`) |
| `SIMULATOR_BACKEND_URL` | empty | URL the simulator task posts to; empty = this backend's own URL as seen on the start request |
| `SIMULATOR_SESSION_MINUTES` | `10` | fixed length of every simulator session (no API parameter can change it) |
| `SIMULATOR_MONTHLY_QUOTA_MINUTES` | `120` | simulator minutes per UTC month, charged a full session up front on each start |

Note: `POST /api/auth/login` and `POST /api/users`'s `password` field is not the raw password —
it's the SHA-256 hex digest of it, computed client-side (`crypto.subtle.digest`) before the
request is sent; the backend bcrypt-hashes that digest. See `CLAUDE.md`'s "Auth design" for why.

## Verification checklist (what "done" looks like end to end)

1. `pytest` passes (hermetic tier).
2. `pytest -m mongo` passes with Docker available (real `2dsphere` behavior).
3. `docker compose up --build` (this directory) boots; `GET /docs` loads, and the configured
   `ROOT_ADMIN_EMAIL` can log in with `ROOT_ADMIN_PASSWORD` with no seed script needed. This step
   now also re-runs step 1's tests automatically as a build gate — see `CLAUDE.md`'s "Docker
   build test gate".
4. `docker compose up --build` from the repo root boots backend + Mongo + `ui-argus` together;
   the frontend at `http://localhost:5173` can reach `http://localhost:8000/docs` from the
   browser (CORS is configured for exactly that origin by default).
5. `python -m scripts.seed_dev_data`, then manually: `POST /api/auth/login` with a seeded user
   returns a token; `GET /api/routes/active` returns the seeded in-progress route with an
   embedded status; `POST /api/alerts` with the seeded truck's device key succeeds, and fails
   with 401 given a wrong/missing key.
6. (final-tweaks) `POST /api/devices/me/report` with a seeded truck's key and
   `{"points": [{"age_s": 1, "lat": 20.66, "lon": -103.35, "speed": 50, "vigilance": "low",
   "last_known": false, "location_age_s": 0}]}` answers 200 with `points.stored: 1` on its
   in-progress route; `GET /api/live` with a user token shows that point in `track`.

**Steps 1–5 have been run for real**, including step 5's device-key checks — see `CLAUDE.md`'s
"Current status". Step 6 (the final-tweaks endpoints) is covered by the hermetic suite
(`tests/test_report.py`, `tests/test_live.py`, ...) but not yet by a manual run against a real
Mongo. What's still unverified: real concurrent load (the route lock and the unique indexes under
real contention), and Mongo running as anything but a single local container.

## Missing / not yet built

Short pointer, not a duplicate — see `CLAUDE.md`'s "Future work" section for the full list with
rationale, and `docs/roadmap.md` for how this fits the whole project's gaps:

- `Report`, `Device`, `Geofence` entities (in the ER diagram, deliberately deferred).
- Real "own truck/route only" scoping for the `truck_driver` role — no `User`↔`Driver`/`Truck`
  link exists in the ER model to scope by yet.
- No refresh-token flow.
- No real-time push for the live dashboard: `ui-argus` polls `GET /api/live` every 5 s.
- The legacy device endpoints (above) are still there; deleting them is a later cleanup.
