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

## Auth

Login (`src/pages/Login.tsx`) is wired to the real `POST /api/auth/login` — see
`src/backend-argus/CLAUDE.md`'s "Auth design" for the full server-side picture. The pieces on
this side:

- **`src/utils/crypto.ts`'s `sha256Hex`**: the password is SHA-256-hashed client-side (Web
  Crypto's `crypto.subtle.digest`) before it's sent — the backend then bcrypts *that* digest, not
  the raw password. This is defense-in-depth on top of TLS, not a replacement for it, and it has
  a real, deliberate consequence: `crypto.subtle` only exists in a secure context (HTTPS or
  `localhost`) by browser spec, so login simply cannot work on a plain-HTTP production origin —
  a forcing function toward HTTPS, not a bug.
- **`src/api/client.ts`**: the one `apiFetch` wrapper every backend call goes through (base URL
  from `VITE_API_BASE_URL`, `Authorization: Bearer` when a token is given, FastAPI `detail`
  error normalization). It also exposes `setUnauthorizedHandler` — a callback fired on any `401`
  — which `AuthContext` uses to clear a stale session the moment any authenticated call rejects
  it, not only when the app first loads with none.
- **`src/context/AuthContext.tsx`**: the single source of truth for "who's logged in" — a
  `{ token, user }` session held in `localStorage` (key `argus.session`), exposed via
  `useAuth()`.
- **`src/components/ProtectedRoute.tsx`**: mounted once, above the whole `AppLayout` route tree
  in `App.tsx` — gates every current and future in-app screen behind a session in one place,
  rather than a per-page check. Redirects to `/login` carrying the originally-requested location
  as router state, so a successful login sends the user back to whatever page they were headed
  to (a deep link, or a session that went stale mid-use) instead of always landing at `/`.
- **Deliberately not done yet**: per-role nav-group hiding in `Sidebar.tsx` (both groups still
  render regardless of role — no confirmed spec for which items each role should see, so this
  pass only swapped the data source from the `CURRENT_USER` fixture to the real session, not
  added new hiding logic), and the "keep me signed in" checkbox is inert UI (not wired to a
  session/localStorage-vs-sessionStorage split). Creating other admin/guardian accounts stays
  possible only via `POST /api/users` directly (curl/Swagger) — no Access panel screen exists
  yet, tracked in `INTEGRATION.md`.

## Docker architecture

Multi-stage `Dockerfile`, mirroring `src/cv-argus`'s Docker-first pattern but simpler — this
is a plain frontend with no native-wheel/glibc-vs-musl concerns, so it uses Alpine rather than
`cv-argus`'s Debian slim base:

- **`deps`**: `npm install`, cached separately so it only re-runs when `package*.json` change.
- **`checks`** (the lint/test/typecheck gate): `COPY . .` then `npm run lint` → `npm run test`
  → `npm run build` (`tsc -b && vite build`) — see "Testing" below for what `npm run test`
  actually runs. Both `dev` and `prod` below depend on this stage *passing*, not just on it
  existing: `dev` is `FROM deps`, not `FROM checks` (so it doesn't inherit `checks`' `dist/`
  output), and instead does `COPY --from=checks /app/package.json ./package.json` — a no-op
  content-wise (`deps` already effectively has that file), but it forces Docker to build and
  pass `checks` before `dev` can be built at all. `build` is simply `FROM checks` directly
  (no extra commands — `checks` already produced `dist/` as part of the gate, so this is just
  the named waypoint `prod` copies it from). Verified directly, not assumed: a plain multi-stage
  `FROM` chain **without** an explicit `COPY --from=`/`FROM checks` dependency does not
  guarantee an unreferenced stage actually builds — the classic builder here will skip it
  depending on file order and `--target`, so the dependency has to be real, not just
  positional.
- **`dev` target** (what `docker-compose.yml` builds): source is bind-mounted over the image
  rather than copied in, so Vite's dev server picks up edits immediately. `vite.config.ts`
  forces `server.watch.usePolling` on unconditionally, since a Docker Desktop bind mount
  (macOS/Windows) crosses a VM boundary that doesn't always propagate inotify events —
  polling costs a little CPU but works everywhere, which a conditional/env-gated setting
  wouldn't guarantee. `server.allowedHosts` is also set to `true` — Vite otherwise 403s any
  request whose `Host` header isn't `localhost`/an IP/an explicit allow-list entry (a
  DNS-rebinding guard), which silently broke `src/it-argus`'s Playwright tests the first time
  they ran against this server under a different hostname (a real bug that testing caught, not
  a preemptive guess — see that module's `CLAUDE.md`). Disabling it is fine specifically because
  this server is dev-only; `prod` is a static nginx bundle with no such check.
- **`build` target**: `FROM checks` (see above) — provides `dist/` for `prod`, not a second
  build.
- **`prod` target** (the Dockerfile's default): the `dist/` bundle served by nginx
  (`nginx.conf` adds the SPA `try_files … /index.html` fallback react-router's client-side
  routes need). The repo-root `docker-compose.yml` that now exists (alongside `src/backend-argus`)
  still builds this module's **`dev`** target, not `prod` — it's a local-integration/dev stack
  (backend + Mongo + `ui-argus` dev server), not a production deploy. There's still no
  `docker-compose.prod.yml` using this `prod` target; write one when an actual production
  deployment (nginx-served bundle, not the Vite dev server) is needed, not before.

Neither final image (`dev`'s bind-mount source copy aside) carries test-only files — `checks`'
`tests`/`*.test.ts(x)` files are never separately `COPY`'d into anything downstream, and Vite's
build only bundles what's actually reachable from `index.html`'s entry point, which no test
file is; confirmed by diffing the production bundle's module count (103, unchanged) before and
after the test suite was added.

## Testing

**Vitest + React Testing Library**, added after this module went its first several sessions
with zero tests (see git history / `docs/roadmap.md` if that gap is ever in question again).
Deliberately not Jest — Vitest shares Vite's config/transform pipeline directly (no separate
babel/ts-jest setup to keep in sync with `vite.config.ts`), and is the standard pairing for a
Vite app.

- **Config lives in `vite.config.ts`'s `test` block**, via `defineConfig` imported from
  `'vitest/config'` (a drop-in superset of plain `'vite'`'s `defineConfig` that also types the
  `test` block) — not a separate `vitest.config.ts`. `environment: 'jsdom'` (every test mounts
  real React components). `globals: false` — every test file imports `describe`/`it`/`expect`/
  etc. explicitly from `'vitest'` rather than relying on ambient globals, so no `tsconfig`
  `"types"` edit was needed to make them typecheck.
- **`src/test/setup.ts`** (the `setupFiles` entry) does two things: imports
  `'@testing-library/jest-dom/vitest'` (registers the DOM matchers — `toBeInTheDocument`,
  `toHaveClass`, etc. — against Vitest's `expect`, and ambiently types them, no separate
  `.d.ts` needed), and calls `afterEach(cleanup)` explicitly. **That second line matters**:
  React Testing Library normally auto-registers its own cleanup via a global `afterEach`, but
  `globals: false` means no test-framework globals are injected, so without this line every
  `render()` in a file would stay mounted into the same `jsdom` `document.body` and leak into
  the next test in that file (multiple-match query errors, flaky selectors) — hit and fixed in
  the same session this was added, not a hypothetical.
- **53 tests across 9 files** (real, currently passing, run inside `docker build` — see "Docker
  architecture" above): `utils/status.test.ts`, `utils/format.test.ts`, `utils/geo.test.ts`
  (the coordinate-shape adapter — covers all 5 accepted shapes plus its error paths, anchoring
  the real API boundary), `utils/crypto.test.ts` (`sha256Hex` against a known digest, hex-pattern/
  determinism checks), `components/StatusPill.test.tsx`, `components/RecordTable.test.tsx`
  (rendering, empty state, click-to-select, `aria-selected`), `components/SearchBox.test.tsx`,
  `components/Sidebar.test.tsx` (nav links, `aria-current`, "soon" badges, sign-out — now wrapped
  in `AuthProvider` with a seeded `localStorage` session instead of the retired `CURRENT_USER`
  fixture), `pages/Login.test.tsx` (controlled inputs, checkbox toggle, and — new — real
  success/failure login flows via `vi.stubGlobal('fetch', ...)`, asserting on `localStorage` and
  navigation). Every other test still renders against fixtures/props directly; `Login`/`Sidebar`
  are the first to exercise a real (mocked) network + session path.
- **Not yet covered**: `AppLayout`, `PageHeader`, `Icon`, `FleetMap` (would need a
  `react-leaflet` mocking strategy — not attempted yet), `ProtectedRoute`/`AuthContext`
  themselves in isolation (covered indirectly through `Login`/`Sidebar`, not directly), and the
  four remaining pages (`LiveOps`, `AlertTriage`, `Fleet`, `Drivers`, `TravelManagement`). Extend
  this suite alongside `src/api/*` as the other screens are wired up, per the user's own steer —
  tests should catch auth/request-shaping bugs as they're introduced, not after.
- **`src/test/setup.ts` polyfills `crypto.subtle`** via Node's `webcrypto` — jsdom provides
  `window.crypto` but not `.subtle`, which `sha256Hex` (and therefore the real `Login` submit
  path) needs; without this, any test exercising it throws immediately. Also clears
  `localStorage` in `afterEach` for the same leakage reason `cleanup()` is already there.
- Run locally with `npm run test` (`vitest run` — single pass, not watch mode; there is no
  separate `npm run test:watch` script yet).

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

**`package-lock.json` now exists** (generated the first time `npm install` actually ran, adding
the Vitest test deps below) **but is not committed yet** — a deliberate pause, not an oversight:
switching the Dockerfile's `deps` stage from `npm install` to `npm ci` is a real behavior change
(strict, reproducible installs vs. permissive resolution) worth a deliberate yes rather than a
side effect of adding tests. See "Next steps" below. All six mockup screens are ported and
render fake data from fixtures.

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

**Auth is now real** (see the "Auth" section above): `Login` calls the real backend, a session
is stored, and every in-app route is gated behind it via `ProtectedRoute`. Still not done:
**per-role nav gating** (the sidebar still shows both role nav-groups — there's a session now,
but no confirmed spec for which items each role should see) and **everything else that talks to
a backend** — every screen besides `Login` still mutates `src/data/fixtures.ts`-derived local
state only ("Guardar"/"Crear" included). All of it is tracked in `INTEGRATION.md`.

## Next steps (not started)

- **Commit `package-lock.json`** (it now exists, generated by this session's `npm install` —
  see "Current status" above) and switch the Dockerfile's `deps` stage from `npm install` to
  `npm ci` once that's done — currently paused on an explicit decision, not forgotten.
- Extend the Vitest suite (see "Testing" above) to the still-uncovered components/pages, and
  alongside `src/api/*` as the remaining screens are wired up, not after.
- **Everything backend-connectivity-related beyond login** — per-screen fetches for Fleet/
  Drivers/Routes/Alerts, per-role nav gating, the real-time strategy for the live dashboard, an
  Access/Users panel, and known gaps in the committed API list itself — is tracked in
  `INTEGRATION.md`, not here, so it doesn't drift out of sync in two places. Read that file
  before wiring any screen up to the backend.
