# backend-argus

FastAPI + MongoDB backend for Argus. Covers User, Truck, Driver, Route, Status_Route, and Alert,
plus login — see `CLAUDE.md` for why it's built this way (auth design, the ER-diagram typo
fixes, the geo storage format, RBAC, and known gaps). This file is the practical "how do I run
it" reference.

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

## Seeding demo data

```sh
python -m scripts.seed_dev_data
```

Loads a small set of trucks/drivers/routes/alerts shaped like `ui-argus/src/data/fixtures.ts`,
plus two logins (`admin@argus.dev` / `guardian@argus.dev`, both password `changeme123`) and one
truck's device API key (printed once, for testing `POST /api/alerts`/`POST /api/routes/:id/status`
with `X-Device-Api-Key` instead of a user token). Safe to re-run — it clears the seeded
collections first.

## Running this module alone in Docker

```sh
docker compose up --build
# -> http://localhost:8000/docs
```

Starts this service plus its own local `mongo` container. `--reload` is on by default in this
compose file (dev loop) — bind-mounts `./app` so edits apply without a rebuild.

## Running the whole stack (backend + Mongo + ui-argus)

From the **repo root** (not this directory):

```sh
docker compose up --build
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

## Verification checklist (what "done" looks like end to end)

1. `pytest` passes (hermetic tier).
2. `pytest -m mongo` passes with Docker available (real `2dsphere` behavior).
3. `docker compose up --build` (this directory) boots; `GET /docs` loads.
4. `docker compose up --build` from the repo root boots backend + Mongo + `ui-argus` together;
   the frontend at `http://localhost:5173` can reach `http://localhost:8000/docs` from the
   browser (CORS is configured for exactly that origin by default).
5. `python -m scripts.seed_dev_data`, then manually: `POST /api/auth/login` with a seeded user
   returns a token; `GET /api/routes/active` returns the seeded in-progress route with an
   embedded status; `POST /api/alerts` with the seeded truck's device key succeeds, and fails
   with 401 given a wrong/missing key.

None of steps 2-5 have been run in the session that first wrote this module — see `CLAUDE.md`'s
"Current status" for exactly what has and hasn't been verified so far.
