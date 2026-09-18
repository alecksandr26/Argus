# ui-argus

The Argus web frontend — a React + TypeScript app (Vite) serving all four roles: **Control
Tower** (`guardian`, read-only monitoring + alert review), **Administration / Logistics**
(`root_admin` full control, `admin` scoped to trucks/drivers/routes), and `truck_driver`. See
`CLAUDE.md` in this directory for why it's built this way, and the top-level `CLAUDE.md` for how
this fits the rest of Argus.

**Current status: every screen calls the real backend.** `src/data/fixtures.ts` is deleted —
`Login`, `Fleet`, `Drivers`, `TravelManagement`, `LiveOps`, and `AlertTriage` all fetch from
`src/backend-argus` via `src/api/*`, and two screens that never had fixture data at all now
exist: **`Access`** (root_admin/admin user management — an admin session is scoped server-side
to guardian accounts only) and **`Profile`** (self-service email/name/phone/password edit for
any role). A third new screen, **`RouteHistory`** (`/history`, "Route history" in the sidebar —
previously a `soon` placeholder labeled "Trip history"), lists completed routes
(`GET /api/routes?status=completed`) each expandable into the alerts raised on that trip
(`GET /api/alerts`, grouped client-side by `id_route` — type, severity, timestamp, and
lat/lon), linking each alert through to its full `AlertTriage` view. Role-based gating is real:
`RequireRole` gates `/access`, `Sidebar.tsx`'s nav items are filtered per role, and
`Fleet`/`Drivers`/`TravelManagement` render read-only (no Add/Save) for a `guardian` session
instead of letting a write attempt fail with an unexplained 403. The live-ops dashboard polls
`GET /api/routes/active` + `GET /api/alerts` every 7s for its map/feed (the interim real-time
strategy — see `INTEGRATION.md` for the still-open WebSocket/SSE question). UI copy is in
English (the design canvas is in Spanish; translated on request). `npm install`, lint,
typecheck, build, and the full Vitest suite (64 tests across 13 files) have all been run for
real against this exact code — see "Running the tests" below and `CLAUDE.md`'s "Current
status"/"Testing" sections (including a real environment gotcha worth reading before you hit it
yourself) — and **`INTEGRATION.md` for what's still genuinely open** (OSRM, a real-time push
mechanism, `Alert.media_url` storage).

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

Needs Node 22+ and npm on your machine.

```bash
cp .env.example .env
npm install
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
npm install    # once
npm run test
```

64 tests across 13 files (Vitest + React Testing Library + jsdom) — component rendering/
interaction (`RecordTable`, `SearchBox`, `Sidebar`, `StatusPill`, `Login`, `RequireRole`) and
pure utility logic (`status.ts`, `format.ts`, `geo.ts`'s coordinate-shape adapter, `crypto.ts`'s
`sha256Hex`), plus real (mocked-`fetch`) network/role-gating flows: `Access.test.tsx`,
`Profile.test.tsx`, and `Fleet.test.tsx` (write-gating: `root_admin` gets an editable panel,
`guardian` gets a read-only one). See `CLAUDE.md`'s "Testing" section for what each file covers,
what isn't covered yet, and two real gotchas hit while building this out (React Testing
Library's auto-cleanup needing an explicit `afterEach`; jsdom's missing `crypto.subtle` needing
a Node `webcrypto` polyfill in `src/test/setup.ts`).

**Two environment gotchas, not code bugs — read this before assuming `npm run test` is broken**:
this project needs **Node ≥22.14** (older Node 20.x hits an `ERR_REQUIRE_ESM` crash from a
broken `html-encoding-sniffer`/`@exodus/bytes` combo in the committed lockfile — ESLint/`tsc`/
the build are unaffected, only Vitest's jsdom environment). Separately, if this checkout lives
under a Windows-mounted path in WSL2 (`/mnt/c/Users/...`), Vitest's workers may simply time out
trying to start at all — run (or at least test) from a native Linux filesystem path instead if
you hit `Timeout waiting for worker to respond` with zero tests actually running. See
`CLAUDE.md`'s "Current status" for the full diagnosis of both.

**This suite now also runs automatically inside `docker build`/`docker compose build`**, as a
real gate — the `checks` stage runs `npm run lint` → `npm run test` → `npm run build` in order,
and both the `dev` and `prod` targets genuinely depend on it passing (verified directly: a
deliberately-broken test fails `docker build` outright, no image produced). See `CLAUDE.md`'s
"Docker architecture" for the exact mechanism.

## Building the production image

The Dockerfile's default target (`prod`) builds the app and serves the static bundle via
nginx — meant for an eventual production deploy sitting next to `src/backend-argus` (the FastAPI
backend, now real code — see its own README for running it), not something you run for
day-to-day development. The repo-root `docker-compose.yml` doesn't use this target yet — it
runs this module's `dev` target instead, for local integration testing against a real backend:

```bash
docker build -t argus/ui-argus:prod .
docker run --rm -p 8080:80 argus/ui-argus:prod
```

## Config

| Variable | Default | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | `http://localhost:8000` | Base URL of `src/backend-argus` (now real code, see its README). Read by `src/api/client.ts` for every backend call, starting with login. |

## Troubleshooting

- **`docker compose up` fails looking for `.env`** — run `cp .env.example .env` first;
  `docker-compose.yml` expects the file to exist even though nothing in it is required yet.
- **Port 5173 already in use** — another Vite dev server (or a previous `docker compose up`)
  is still running; stop it, or change the host-side port in `docker-compose.yml`'s `ports:`.
- **Edits under `src/` aren't showing up in the Docker dev server** — this shouldn't happen
  (`vite.config.ts` forces polling specifically so bind-mount edits are always picked up); if
  it does, restart the container rather than digging into it first.
- **`npm ci` instead of `npm install`** — the Dockerfile still uses `npm install`.
  `package-lock.json` **is committed** (since the "adding unit tests for UI" commit) — switching
  the Dockerfile's `deps` stage to `npm ci` for reproducible installs is a real, small remaining
  step, not blocked on anything anymore; see `CLAUDE.md`'s "Next steps".
- **`npm run test` hangs or crashes outright** — see the two environment gotchas called out in
  "Running the tests" above (Node version, WSL2 `/mnt/c` path) before assuming it's a code bug.

## Missing / not yet built

Short pointer, not a duplicate — see `INTEGRATION.md` for the full per-screen breakdown, and
`docs/roadmap.md` for how this fits the whole project's gaps:

- No Reports panel, no Geofence management, no dedicated Truck Driver screen — none of these
  were in the first UI pass (no committed API/table effort behind them). Access (Users) and
  Profile, previously in this same "not built" list, are done now.
- `Alert.media_url`'s storage/serving story (S3? the backend directly?) isn't decided anywhere.
- No real-time push mechanism for the live dashboard — currently polling every 7s.
- OSRM integration for `TravelManagement`'s create form — `destination_coordinates`/
  `estimated_arrival` are still stubbed.
- No shared data-fetching/caching layer (TanStack Query or similar) — every screen does its own
  `useEffect` fetch.
