# CLAUDE.md — ui-argus

This file explains why `ui-argus` is built the way it is. See `README.md` in this directory
for practical "how do I run this" instructions, and the top-level `CLAUDE.md` for how this
module fits the rest of Argus (the ER model, the planned FastAPI backend, the two UI roles).

## What this is

The Argus web frontend: a single React app serving both MVP roles by role-based navigation
(one login, `role` on the `User` entity decides what's visible) rather than two separate
portal apps — see the "Argus — Mockups de UI" design canvas for the actual screen designs this
scaffold follows, and the conversation that produced it for why Reports, Access (Users), and
Geofences were cut from the first UI pass (no committed API/table effort yet for those).

**UI copy and route paths are in English** (`/fleet`, `/drivers`, `/routes`, `/alerts/:id`),
even though the design canvas is in Spanish — translated on request. Domain field names still
follow the ER model. If the copy ever needs to go back to Spanish, it's all in the
`src/pages/*` / `src/components/*` JSX and `src/utils/status.ts` (the label map), plus
`src/data/fixtures.ts` for the fake alert text.

## Stack choices

- **Vite, not Create React App or Next.js.** No server-side rendering or backend-for-frontend
  is needed — the FastAPI backend (planned, not built yet) is a separate service the browser
  talks to directly per the top-level CLAUDE.md's architecture, so a pure client-side SPA is
  the right shape, and Vite's dev server + esbuild-based build is materially faster than CRA's
  webpack pipeline for that shape.
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
  routes need). This is what's meant to join the FastAPI backend in the planned cloud Docker
  Compose stack — there's no `docker-compose.prod.yml` yet because that stack (backend +
  OSRM + Mongo, per the top-level CLAUDE.md) doesn't exist yet either; write it alongside the
  backend, not before it.

## Current status

**All six mockup screens are ported and render fake data — not build-verified.** No
`npm`/`node_modules`/`node` and no working Docker daemon have been available in any environment
this was authored in, so every file is hand-authored to match what the real toolchain would
produce and has **never** been run through `npm install`, `tsc`, `npm run build`, `npm run
dev`, or `docker compose up`. Treat the first real run as a verification step, not a formality
— a version-range conflict in `package.json` or a stray type error could still surface. There
is also no `package-lock.json` yet for the same reason — the Dockerfile's `deps` stage uses
`npm install` rather than `npm ci` until one is generated and committed (see the Dockerfile's
comment on this).

What exists now:
- `App.tsx` mounts `AppLayout` (sidebar + `<Outlet/>`) as a layout route around the five
  in-app screens; `/login` sits outside it. Every screen is a real component — `PageStub` is
  deleted.
- **`src/types.ts`** — TypeScript interfaces for `User`/`Truck`/`Driver`/`Route`/`StatusRoute`/
  `Alert`, field names copied verbatim from the ER model (`docs/designs/ER-model.drawio.xml`)
  so they line up 1:1 with the backend's Pydantic models when those exist. The `*_status`
  string unions are a frontend guess (the ER model doesn't enumerate `operative_status`
  values) and must be reconciled with the backend.
- **`src/data/fixtures.ts`** — the fake ("foo") data every screen reads: 8 trucks, 8 drivers,
  9 routes, 6 live-status rows, 7 alerts, one user, kept name-consistent with the mockups.
  `MOCK_NOW` is a fixed clock so relative timestamps ("40s ago") don't drift. **Delete this
  file when the API client lands** — `src/types.ts` stays.
- **`src/utils/`** — `format.ts` (relative time, clock, dates, all against `MOCK_NOW`) and
  `status.ts` (status-union → English label + colour "tone", the one place the pill/tile
  colour language lives). Named `utils/` not `lib/` because the repo-root `.gitignore` (a
  Python template) ignores `lib/` at any depth.
- **`src/components/`** — `Sidebar`, `AppLayout`, `Icon` (shared inline-SVG set),
  `PageHeader`, `SearchBox`, `RecordTable` (the shared Fleet/Drivers/Routes table),
  `StatusPill`.
- **`src/pages/`** — `Login` (controlled form, submit just routes to `/`), `LiveOps` (stat
  tiles + schematic fleet map with lat/lon-projected markers + filterable alert feed linking
  to triage), `AlertTriage` (looks the alert up by `:alertId`, model-score bars, review
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

- Run `npm install` + `npm run build` and fix whatever the first real type-check / bundle
  surfaces.
- Generate and commit `package-lock.json` on the first real `npm install`, then switch the
  Dockerfile's `deps` stage from `npm install` to `npm ci`.
- **Everything backend-connectivity-related** — the API client, auth/session, per-screen
  fetches, the real-time strategy for the live dashboard, and known gaps in the committed API
  list itself — is tracked in `INTEGRATION.md`, not here, so it doesn't drift out of sync in
  two places. Read that file before wiring any screen up to the backend.
