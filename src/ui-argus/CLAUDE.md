# CLAUDE.md — ui-argus

This file explains why `ui-argus` is built the way it is. See `README.md` in this directory
for practical "how do I run this" instructions, and the top-level `CLAUDE.md` for how this
module fits the rest of Argus (the ER model, `src/backend-argus`, the three actor roles).

## What this is

The Argus web frontend: a single React app serving every MVP role by role-based navigation
(one login, `role` on the `User` entity decides what's visible) rather than separate portal
apps per role — see the "Argus — Mockups de UI" design canvas for the actual screen designs
this scaffold follows, and the conversation that produced it for why Reports, Access (Users),
and Geofences were cut from the first UI pass (no committed API/table effort yet for those). The
screens built so far are shaped around the `root_admin`/`guardian` roles' workflows (fleet,
drivers, routes, alerts); `truck_driver` has a `Role` value and shows up in role-label logic
(`Sidebar.tsx`) but no dedicated screen exists yet — per the root `CLAUDE.md`, that role mainly
*receives* alerts/status rather than manages the fleet, so it may not need one.

**UI copy and route paths are in English** (`/fleet`, `/drivers`, `/routes`, `/alerts/:id`),
even though the design canvas is in Spanish — translated on request. Domain field names still
follow the ER model. If the copy ever needs to go back to Spanish, it's all in the
`src/pages/*` / `src/components/*` JSX and `src/utils/status.ts` (the label map), plus
`src/data/fixtures.ts` for the fake alert text.

## Stack choices

- **Vite, not Create React App or Next.js.** No server-side rendering or backend-for-frontend
  is needed — `src/backend-argus` (the FastAPI backend, now real code) is a separate service the
  browser talks to directly per the top-level CLAUDE.md's architecture, so a pure client-side
  SPA is the right shape, and Vite's dev server + esbuild-based build is materially faster than
  CRA's webpack pipeline for that shape.
- **TypeScript, not plain JS.** This is a titulación project whose grading criteria
  (`docs/criterios/`) explicitly reward justified language choices; static typing catches
  integration errors against the backend's Pydantic models at compile time rather than at
  runtime in front of a Control Tower operator, which matters more here than in a typical
  internal tool given the safety-monitoring use case.
- **react-router-dom**, because the app is genuinely multi-page (six+ screens across two
  roles) with URLs worth sharing/bookmarking (e.g. a direct link to one alert's triage view),
  not a single-view app that could get away without a router.
- **Plain CSS custom properties, not a component/styling library** (Tailwind, MUI, etc.) —
  not decided against, just not decided yet. `src/index.css` carries over the design tokens
  (colors, fonts) from the approved mockups verbatim so real components stay visually
  consistent with what was reviewed; if/when a styling approach is picked, those tokens are
  the source of truth to carry into it, not something to re-derive from the canvas again.
- **`react-leaflet` + `leaflet` for the map** (Live operations screen, `src/components/
  FleetMap.tsx`). This was already the top-level `CLAUDE.md`'s decision ("a React frontend
  using react-leaflet to render the OSRM route and live truck/alert status"); it's now a real
  dependency, not just aspirational. `react-leaflet` **5.x** — the 4.x line pins React 18 and
  won't resolve against this project's React 19. Tiles are the standard (light) OpenStreetMap
  basemap — keyless, no account, no billing; the map panel is a deliberate light island in the
  dark UI. Google Maps was considered and rejected: its "free" tier still needs a
  billing-enabled Google Cloud key shipped in the browser bundle, and it contradicts the
  design already written down. OSRM route-line rendering stays deferred ("mejora futura") —
  the map currently just shows live truck positions from fixtures. Full write-up (both this
  and the coordinate-adapter decision, with the Google Maps / Amazon Location rejection
  reasoning): `docs/designs/frontend-map-and-coordinates.md`.

## Docker architecture

Multi-stage `Dockerfile`, mirroring `src/cv-argus`'s Docker-first pattern but simpler — this
is a plain frontend with no native-wheel/glibc-vs-musl concerns, so it uses Alpine rather than
`cv-argus`'s Debian slim base:

- **`dev` target** (what `docker-compose.yml` builds): source is bind-mounted over the image
  rather than copied in, so Vite's dev server picks up edits immediately. `vite.config.ts`
  forces `server.watch.usePolling` on unconditionally, since a Docker Desktop bind mount
  (macOS/Windows) crosses a VM boundary that doesn't always propagate inotify events —
  polling costs a little CPU but works everywhere, which a conditional/env-gated setting
  wouldn't guarantee.
- **`build` target**: runs `npm run build`, produces `dist/`. Not run directly — only the base
  for `prod`.
- **`prod` target** (the Dockerfile's default): the `dist/` bundle served by nginx
  (`nginx.conf` adds the SPA `try_files … /index.html` fallback react-router's client-side
  routes need). The repo-root `docker-compose.yml` that now exists (alongside `src/backend-argus`)
  still builds this module's **`dev`** target, not `prod` — it's a local-integration/dev stack
  (backend + Mongo + `ui-argus` dev server), not a production deploy. There's still no
  `docker-compose.prod.yml` using this `prod` target; write one when an actual production
  deployment (nginx-served bundle, not the Vite dev server) is needed, not before.

## Current status

**Build-verified, as of the `src/backend-argus` change**: `docker compose up --build` (both this
module's own compose file and the new repo-root one) has been run for real — `npm install`
(181 packages, 0 vulnerabilities), `npx tsc --noEmit`, and `npm run build` (Vite production
build, 103 modules) all pass cleanly, and the dev server serves `http://localhost:5173`
correctly inside the container. This was this module's first-ever real toolchain run; earlier
revisions of this file said exactly that hadn't happened yet — it has now. `types.ts`/
`fixtures.ts` and a few consuming components (`AlertTriage.tsx`, `LiveOps.tsx`,
`Sidebar.tsx`) were updated in that same session to match `src/backend-argus`'s corrected field
names (`reviewed_by_operator`, the 3→2-role `Role` enum, binary `not_drowsy`/`drowsy` AI
scores) — see that module's `CLAUDE.md` for the full old→new field table.

There is still no `package-lock.json` committed — the Dockerfile's `deps` stage uses
`npm install` rather than `npm ci` until one is generated and committed (see the Dockerfile's
comment on this). All six mockup screens are ported and render fake data from fixtures.

What exists now:
- `App.tsx` mounts `AppLayout` (sidebar + `<Outlet/>`) as a layout route around the five
  in-app screens; `/login` sits outside it. Every screen is a real component — `PageStub` is
  deleted.
- **`src/types.ts`** — TypeScript interfaces for `User`/`Truck`/`Driver`/`Route`/`StatusRoute`/
  `Alert`. Field names now line up 1:1 with `src/backend-argus`'s Pydantic schemas (not just the
  ER diagram verbatim — a couple of the diagram's own typos are fixed here to match the real
  backend; see that module's `CLAUDE.md` for the full table). `Role` is reconciled too
  (`'root_admin' | 'guardian' | 'truck_driver'`, matching the backend's enum exactly). The
  `*_status` string unions for `Truck`/`Driver`/`Route`/`Status_Route`/`Alert` are still a
  frontend guess — the ER model doesn't enumerate `operative_status` values — but they do match
  what `src/backend-argus/app/models/common.py` actually implements, since that backend was
  built reading these fixtures as the reference; still worth a final glance across both files
  before treating them as permanently locked.
- **`src/data/fixtures.ts`** — the fake ("foo") data every screen reads: 8 trucks, 8 drivers,
  9 routes, 6 live-status rows, 7 alerts, one user, kept name-consistent with the mockups.
  `MOCK_NOW` is a fixed clock so relative timestamps ("40s ago") don't drift. **Delete this
  file when the API client lands** — `src/types.ts` stays.
- **`src/utils/`** — `format.ts` (relative time, clock, dates, all against `MOCK_NOW`),
  `status.ts` (status-union → English label + colour "tone", the one place the pill/tile
  colour language lives), and `geo.ts` (`toLatLng`: `Coordinates` → Leaflet's `[lat,lng]`
  tuple; `normalizeCoordinates`: the API-boundary adapter that folds GeoJSON / `{lat,lng}` /
  string coordinate payloads into the canonical `{lat,lon}` shape). Named `utils/` not `lib/`
  because the repo-root `.gitignore` (a Python template) ignores `lib/` at any depth.
- **`src/components/`** — `Sidebar`, `AppLayout`, `Icon` (shared inline-SVG set),
  `PageHeader`, `SearchBox`, `RecordTable` (the shared Fleet/Drivers/Routes table),
  `StatusPill`, `FleetMap` (the `react-leaflet` map on Live operations — OpenStreetMap tiles,
  one `divIcon` truck marker per live-status row + a detail popup; presentational, `LiveOps`
  builds the marker array).
- **`src/pages/`** — `Login` (controlled form, submit just routes to `/`), `LiveOps` (stat
  tiles + an interactive `react-leaflet` fleet map fed by fixture coordinates + filterable
  alert feed linking to triage), `AlertTriage` (looks the alert up by `:alertId`, model-score
  bars, review
  checkbox + notes as local state), `Fleet`/`Drivers` (search-filter + row-select → edit
  panel, add/edit against a local `useState` copy), `TravelManagement` (route table + a
  working "New route" create form).
- `src/index.css` carries the mockups' dark-theme design tokens plus the shared component
  classes (`.pill`, `.btn`, `.data-table`, `.input`, `.panel`, …); screens keep inline styles
  for one-off layout, matching how the mockups themselves are written.

Still not done: **auth, role-gating, and anything that talks to a backend** — the sidebar
shows both role nav-groups because there's no session to gate on, `Login` doesn't
authenticate, and every "Guardar"/"Crear" mutates local state only. All of it is tracked in
`INTEGRATION.md`.

## Next steps (not started)

- Generate and commit `package-lock.json` on the first real `npm install` (it will also pin
  `leaflet` / `react-leaflet` / `@types/leaflet`), then switch the Dockerfile's `deps` stage
  from `npm install` to `npm ci`.
- **Everything backend-connectivity-related** — the API client, auth/session, per-screen
  fetches, the real-time strategy for the live dashboard, and known gaps in the committed API
  list itself — is tracked in `INTEGRATION.md`, not here, so it doesn't drift out of sync in
  two places. Read that file before wiring any screen up to the backend.
