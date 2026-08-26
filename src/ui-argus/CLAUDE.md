# CLAUDE.md — ui-argus

This file explains why `ui-argus` is built the way it is. See `README.md` in this directory
for practical "how do I run this" instructions, and the top-level `CLAUDE.md` for how this
module fits the rest of Argus (the ER model, the planned FastAPI backend, the two UI roles).

## What this is

The Argus web frontend: a single React app serving both MVP roles by role-based navigation
(one login, `role` on the `User` entity decides what's visible) rather than two separate
portal apps — see the "Argus — Mockups de UI" design canvas for the actual screen designs this
scaffold's route names mirror, and the conversation that produced it for why Reports, Access
(Users), and Geofences were cut from the first UI pass (no committed API/table effort yet for
those).

## Stack choices

- **Vite, not Create React App or Next.js.** No server-side rendering or backend-for-frontend
  is needed — the FastAPI backend (planned, not built yet) is a separate service the browser
  talks to directly per the top-level CLAUDE.md's architecture, so a pure client-side SPA is
  the right shape, and Vite's dev server + esbuild-based build is materially faster than CRA's
  webpack pipeline for that shape.
- **TypeScript, not plain JS.** This is a titulación project whose grading criteria
  (`docs/criterios/`) explicitly reward justified language choices; static typing catches
  integration errors against the backend's Pydantic models at compile time rather than at
  runtime in front of a Torre de Control operator, which matters more here than in a typical
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

**Scaffold only, not build-verified.** Neither `npm`/`node_modules` nor a working Docker
daemon were available in the environment this was authored in (only a bare `node` binary and
a `docker` CLI with no daemon running), so every file here was hand-authored to match what
`npm create vite@latest -- --template react-ts` plus `react-router-dom` would produce — it has
not actually been run through `npm install`, `npm run build`, `npm run dev`, or
`docker compose up`. Treat the first real run of any of these as a verification step, not a
formality: something as simple as a version-range conflict in `package.json` could still
surface there. There is also no `package-lock.json` yet for the same reason — the Dockerfile's
`deps` stage uses `npm install` rather than `npm ci` until one is generated and committed
(see the Dockerfile's comment on this).

Within that scaffold:
- `App.tsx` wires a route per mockup screen (`/`, `/alertas/:alertId`, `/flota`,
  `/conductores`, `/rutas`, `/login`), each rendering `PageStub` — a placeholder, not the real
  screen. There is no auth, no role-based guarding, and no API calls anywhere yet.
- `src/index.css` carries the mockups' dark-theme design tokens (colors, font stack) so
  whichever screen gets built first starts from the same visual baseline already reviewed,
  rather than a freshly-guessed one.

## Next steps (not started)

- Port the mockup screens into real components, most usefully starting with the shared
  `Sidebar` (it appears on 5 of the 6 screens) and `Login`.
- Generate and commit `package-lock.json` on the first real `npm install`, then switch the
  Dockerfile's `deps` stage from `npm install` to `npm ci`.
- **Everything backend-connectivity-related** — the API client, auth/session, per-screen
  fetches, the real-time strategy for the live dashboard, and known gaps in the committed API
  list itself — is tracked in `INTEGRATION.md`, not here, so it doesn't drift out of sync in
  two places. Read that file before wiring any screen up to the backend.
