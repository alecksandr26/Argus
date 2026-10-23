# ui-argus

The Argus web frontend — a React + TypeScript app (Vite) serving all four roles: **Control
Tower** (`guardian`, read-only monitoring + closing incidents), **Administration / Logistics**
(`root_admin` full control, `admin` scoped to trucks/drivers/routes), and `truck_driver`. See
`CLAUDE.md` in this directory for why it's built this way, and the top-level `CLAUDE.md` for how
this fits the rest of Argus.

## What it shows

Every screen calls the real `src/backend-argus` API through `src/api/*`:

- **Live operations** (`/`): one `GET /api/live` poll every 5 s (paused while the browser tab is
  hidden). A map with one marker per active route, coloured by the route's alert state
  (normal / medium / critical, hollow when it's a last-known position), each route's GPS path
  and a pin per open-incident alert. Beside it, the **open incidents** in tier sections
  (Critical → Medium → Older than 1 h), grouped by route, each alert with its position
  ("Last known (N old)" / "Position unknown" where that applies), "Show on map", "Driver OK
  since" and a "Recovered after lost connection" badge for alerts replayed from the truck's
  dead-letter queue. Guardian and root_admin **close incidents** (a note is required for a
  critical one); a "… incident closed · Closed HH:MM" confirmation stays until the next close.
  Root_admin and admin **complete or stop** a route (a reason is required to stop).
- **Route view** (`/routes/:id/live`, optionally `?alert=<id>`; the old `/alerts/:id` links still
  work): one route, active or ended — the whole GPS path (seeded from
  `GET /api/routes/{id}/track`, then the live points), the truck followed on the map ("Follow
  truck" after a "Show on map"), its open and closed incidents, and the focused alert's detail.
- **Alert history** (`/alerts/history`): closed incidents with their close time, note and alerts.
- **Route history** (`/history`): completed and stopped trips, each with its GPS path, alert
  pins and alerts (each with "Show on map").
- **Routes**, **Fleet**, **Drivers**, **Access** (user management, root_admin/admin) and
  **Profile** (self-service account edit). Fleet issues each truck's device key
  (`argus-device.env`); root_admin also gets the cloud **Fleet simulator** switch on Live
  operations.

The panic button and grip sensor were removed from the system, and from the UI; old panic alerts
still read as "Panic button". UI copy is in English (the design canvas is in Spanish).
**`INTEGRATION.md`** lists what's still open (OSRM, a push channel instead of polling,
`Alert.media_url` storage).

## Quick start (Docker — recommended)

No local Node/npm install needed; everything runs in the container.

```bash
cp .env.example .env
docker compose up --build   # first run, or after a Dockerfile/package.json change
docker compose up           # every run after that — reuses the already-built image
```

Open http://localhost:5173. Source is bind-mounted, so edits under `src/` hot-reload without
rebuilding the image — you'll rarely need `--build` again once the image exists (`docker images`
will show `ui-argus`'s image).

## Quick start (local Node, no Docker)

Needs **Node ≥22.14** (Node 24 works) and npm.

```bash
cp .env.example .env
npm ci
npm run dev
```

## Other commands

| Command | What it does |
|---|---|
| `npm run dev` | Vite dev server with hot reload |
| `npm run build` | Type-checks (`tsc -b`) then produces a production bundle in `dist/` |
| `npm run preview` | Serves the `dist/` bundle locally, to sanity-check a production build |
| `npm run lint` | ESLint over the whole project |
| `npm run test` | Runs the Vitest unit-test suite once (`vitest run`, not watch mode) |

## Running the tests

```bash
npm ci         # once
npm run test
```

246 tests across 30 files (Vitest + React Testing Library + jsdom): the `/api/live` merge and
de-duplication, the incident tiers, positions and map layers as pure functions; the polling
hooks; the incident feed, the close / complete / stop dialogs (including the 503 "Route is busy,
try again" retry); and every page with a mocked `fetch` (Leaflet is mocked too — what the map
actually draws is only checked in a browser, by `src/it-argus`). See `CLAUDE.md`'s "Testing"
section for what each file covers.

**Two environment gotchas, not code bugs — read this before assuming `npm run test` is broken**:
- **Node ≥22.14.** Node 20.x crashes Vitest's jsdom environment with `ERR_REQUIRE_ESM`
  (`html-encoding-sniffer` requiring the ESM-only `@exodus/bytes`). ESLint, `tsc` and the build
  are unaffected.
- **WSL2 `/mnt/c/...` checkouts.** Vitest's workers may time out starting (`Timeout waiting for
  worker to respond`, zero tests run). Copy the module to a native Linux path and test there:

  ```bash
  rsync -a --delete --exclude node_modules --exclude dist src/ui-argus/ ~/ui-run/
  cd ~/ui-run && npm ci && npm test && npm run build
  ```

`npm ci` works since the lockfile fix: `package-lock.json` had been committed with a
machine-local `file:../../../../../../mnt/c/...` root version and the same prefix on every
package key, so `npm ci` saw every dependency as missing. See `CLAUDE.md`'s "Current status".

**This suite also runs inside `docker build`/`docker compose build`**, as a real gate — the
`checks` stage runs `npm run lint` → `npm run test` → `npm run build` in order, and both the
`dev` and `prod` targets depend on it passing. See `CLAUDE.md`'s "Docker architecture".

## Building the production image

The Dockerfile's default target (`prod`) builds the app and serves the static bundle via
nginx. The repo-root `docker-compose.yml` runs this module's `dev` target instead, for local
integration testing against a real backend; the AWS deployment (`src/infra-argus`) serves the
built bundle from S3 + CloudFront.

```bash
docker build -t argus/ui-argus:prod .
docker run --rm -p 8080:80 argus/ui-argus:prod
```

## Config

| Variable | Default | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | `http://localhost:8000` | Base URL of `src/backend-argus`. Read by `src/api/client.ts` for every backend call. |

Polling intervals are constants in `src/constants.ts`, not config: `/api/live` every 5 s (paused
while the tab is hidden), Alert history every 5 s, the simulator status every 10 s while a
session is open.

## Troubleshooting

- **`docker compose up` fails looking for `.env`** — run `cp .env.example .env` first;
  `docker-compose.yml` expects the file to exist even though nothing in it is required yet.
- **Port 5173 already in use** — another Vite dev server (or a previous `docker compose up`)
  is still running; stop it, or change the host-side port in `docker-compose.yml`'s `ports:`.
- **Edits under `src/` aren't showing up in the Docker dev server** — this shouldn't happen
  (`vite.config.ts` forces polling specifically so bind-mount edits are always picked up); if
  it does, restart the container rather than digging into it first.
- **The Dockerfile still uses `npm install`.** `npm ci` works now; switching the `deps` stage
  to it is a small open step (see `CLAUDE.md`'s "Next steps").
- **"Route is busy, try again" when stopping or completing a route** — another request held the
  route's lock on the backend; the dialog stays open, press the button again.
- **An incident closed long ago is missing from Alert history** — it reads the 200 most recent
  closed incidents by their last alert time (see `CLAUDE.md`'s "Known limits").
- **`npm run test` hangs or crashes outright** — see the two environment gotchas in "Running
  the tests" above before assuming it's a code bug.

## Missing / not yet built

Short pointer, not a duplicate — see `INTEGRATION.md` for the full per-screen breakdown, and
`docs/roadmap.md` for how this fits the whole project's gaps:

- No Reports panel, no Geofence management, no dedicated Truck Driver screen.
- `Alert.media_url`'s storage/serving story (S3? the backend directly?) isn't decided anywhere.
- No real-time push channel for the live screens — `/api/live` is polled every 5 s.
- OSRM integration for `TravelManagement`'s create form — `destination_coordinates`/
  `estimated_arrival` are still stubbed.
- No shared data-fetching/caching layer (TanStack Query or similar) for the non-live screens.
