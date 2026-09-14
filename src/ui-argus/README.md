# ui-argus

The Argus web frontend — a React + TypeScript app (Vite) serving the two MVP roles,
**Control Tower** (live monitoring) and **Administration / Logistics** (fleet/driver/route
management). See `CLAUDE.md` in this directory for why it's built this way, and the
top-level `CLAUDE.md` for how this fits the rest of Argus.

**Current status: login is wired to the real backend; the other five screens still run on fake
data.** `Login` calls `POST /api/auth/login` for real (client-side SHA-256 pre-hash, see
`CLAUDE.md`'s "Auth" section), stores the session, and every in-app route is now gated behind it
via `ProtectedRoute` — the live-ops dashboard, alert triage, and the Fleet/Drivers/Routes CRUD
screens are real components that still read from `src/data/fixtures.ts` for their own data,
search filters, row selection, edit panels and create forms all working against local state. The
live-ops dashboard has a real interactive map (**react-leaflet** + OpenStreetMap tiles, keyless)
with a truck marker per live-status row. UI copy is in English (the design canvas is in Spanish;
translated on request). What's **not** there yet: role-gating of the nav itself (both role groups
still render), and any real backend call beyond login.
`npm install`, lint, typecheck, build, and a real Vitest unit-test suite (53 tests) have all
been run for real — see "Running the tests" below and CLAUDE.md's "Current status"/"Testing"
sections — and **`INTEGRATION.md` for the checklist of where backend-connectivity code needs to
land** (per screen, plus the cross-cutting gaps — real-time strategy, per-role nav gating).

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

53 tests across 9 files (Vitest + React Testing Library + jsdom) — component rendering/
interaction (`RecordTable`, `SearchBox`, `Sidebar`, `StatusPill`, `Login`) and pure utility logic
(`status.ts`, `format.ts`, `geo.ts`'s coordinate-shape adapter, `crypto.ts`'s `sha256Hex`).
`Login`/`Sidebar` now mock `fetch`/seed `localStorage` to exercise the real auth flow end to
end — everything else still renders against fixtures/props directly. See `CLAUDE.md`'s
"Testing" section for what each file covers, what isn't covered yet, and a real gotcha hit while
setting this up (React Testing Library's auto-cleanup needing an explicit `afterEach`, and
jsdom's missing `crypto.subtle` needing a Node `webcrypto` polyfill in `src/test/setup.ts`).

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
- **`npm ci` instead of `npm install`** — the Dockerfile deliberately uses `npm install`.
  `package-lock.json` now exists locally (generated by the first real `npm install`, when the
  test tooling was added) but isn't committed yet — a deliberate pause on that specific decision,
  not an oversight (see the Dockerfile's comment, and `CLAUDE.md`'s "Next steps"). Once it's
  committed, switch the Dockerfile's `deps` stage to `npm ci` for reproducible installs.

## Missing / not yet built

Short pointer, not a duplicate — see `INTEGRATION.md` for the full per-screen breakdown, and
`docs/roadmap.md` for how this fits the whole project's gaps:

- Login, session storage, and route guarding are real now (`src/api/*`, `src/context/
  AuthContext.tsx`, `src/components/ProtectedRoute.tsx`) — every other screen still reads
  `src/data/fixtures.ts` for its own data, and the sidebar still shows both role nav-groups
  unconditionally (no per-role nav gating yet, tracked in `INTEGRATION.md`).
- No Reports panel, no Access/Users panel (creating other admins/guardians is only possible via
  `POST /api/users` directly, not through the UI yet), no Geofence management, no dedicated
  Truck Driver screen.
- `Alert.media_url`'s storage/serving story (S3? the backend directly?) isn't decided anywhere.
- No real-time strategy decided for the live dashboard (polling vs. WebSocket/SSE).
