# CLAUDE.md — ui-argus

This file explains why `ui-argus` is built the way it is. See `README.md` in this directory
for practical "how do I run this" instructions, and the top-level `CLAUDE.md` for how this
module fits the rest of Argus (the ER model, `src/backend-argus`, the four actor roles).

## What this is

The Argus web frontend: a single React app serving every MVP role by role-based navigation
(one login, `role` on the `User` entity decides what's visible) rather than separate portal
apps per role — see the "Argus — Mockups de UI" design canvas for the actual screen designs
this scaffold follows. Reports and Geofences are still cut (no committed API/table effort yet
for those); **Access (Users) is no longer cut** — `Access.tsx` now exists, root_admin/admin
only. The screens are shaped around `root_admin`/`admin`/`guardian` workflows (fleet, drivers,
routes, alerts, user management); `truck_driver` has a `Role` value and shows up in role-label
logic (`Sidebar.tsx`) but no dedicated screen exists yet — per the root `CLAUDE.md`, that role
mainly *receives* alerts/status rather than manages the fleet, so it may not need one. Every
role, including `truck_driver`, does get `Profile.tsx` — the self-service account page.

**UI copy and route paths are in English** (`/fleet`, `/drivers`, `/routes`, `/alerts/:id`,
`/access`, `/profile`), even though the design canvas is in Spanish — translated on request.
Domain field names still follow the ER model. If the copy ever needs to go back to Spanish,
it's all in the `src/pages/*` / `src/components/*` JSX and `src/utils/status.ts` (the label
map).

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
- **Now done**: per-role nav gating. `Sidebar.tsx`'s `NavItem` type carries an optional `roles`
  allow-list, filtered per-group before render — `Fleet`/`Drivers`/`Routes & trips` are visible
  to `root_admin`/`admin`/`guardian` (read-only for guardian, enforced inside those pages, not
  by hiding the nav item) but not `truck_driver`; `Access` is `root_admin`/`admin` only. A
  second component, `src/components/RequireRole.tsx`, adds a route-level version of the same
  check (used to gate `/access` directly, in case someone types the URL rather than clicking
  the nav item). Creating other accounts is now a real screen too — `Access.tsx`
  (`root_admin`/`admin`, root_admin sees/creates any role, `admin` is scoped server-side to
  guardian accounts only) — no more curl/Swagger required.
- **Still not done**: the "keep me signed in" checkbox is inert UI (not wired to a
  session/localStorage-vs-sessionStorage split).

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
- **13 test files** (real; see "Current status" below for the environment caveat on actually
  running them): the original 9 — `utils/status.test.ts`, `utils/format.test.ts`,
  `utils/geo.test.ts` (the coordinate-shape adapter), `utils/crypto.test.ts`,
  `components/StatusPill.test.tsx`, `components/RecordTable.test.tsx`,
  `components/SearchBox.test.tsx`, `components/Sidebar.test.tsx` (extended with role-gating
  cases: `Access` hidden from a guardian session but visible, un-"soon", for `root_admin`;
  `Fleet`/`Drivers`/`Routes & trips` hidden from `truck_driver`), `pages/Login.test.tsx` — plus
  four new ones added alongside the backend-wiring pass: `components/RequireRole.test.tsx`
  (allowed role renders the gated route, disallowed/no session redirects),
  `pages/Access.test.tsx` (root_admin sees every role in the create form; an admin session's
  role picker is locked to `guardian`; the two destructive actions — Deactivate vs. Delete
  permanently — are both present and distinct), `pages/Profile.test.tsx` (loads/edits the
  caller's own fields via `GET`/`PUT /api/auth/me`, asserts the request body never carries a
  `role`/`is_active` key), `pages/Fleet.test.tsx` (write-gating: `root_admin` sees an editable
  panel with "Add truck", `guardian` sees a read-only "View truck" panel with disabled inputs
  and no Save button).
- **Not yet covered**: `AppLayout`, `PageHeader`, `Icon`, `FleetMap` (would need a
  `react-leaflet` mocking strategy — not attempted yet), `Drivers.test.tsx`/
  `TravelManagement.test.tsx` (same write-gating shape as `Fleet.test.tsx`, not yet duplicated),
  `LiveOps`/`AlertTriage` (the two data-heaviest pages — multiple parallel fetches each).
- **`src/test/setup.ts` polyfills `crypto.subtle`** via Node's `webcrypto` — jsdom provides
  `window.crypto` but not `.subtle`, which `sha256Hex` (and therefore the real `Login` submit
  path) needs; without this, any test exercising it throws immediately. Also clears
  `localStorage` in `afterEach` for the same leakage reason `cleanup()` is already there.
- Run locally with `npm run test` (`vitest run` — single pass, not watch mode; there is no
  separate `npm run test:watch` script yet).

## Current status

**Every screen now calls the real backend.** `src/data/fixtures.ts` is deleted — `Fleet`,
`Drivers`, `TravelManagement`, `LiveOps`, and `AlertTriage` all fetch from `src/backend-argus`
via `src/api/*`, and two new screens (`Access.tsx`, `Profile.tsx`) exist that never had fixture
data at all. `src/types.ts`'s `Role` grew from three members to four (`root_admin` / `admin` /
`guardian` / `truck_driver`) alongside the backend's own RBAC change — see the top-level
`CLAUDE.md` and `src/backend-argus/CLAUDE.md` for what `admin` is and how its scoping works.
`npx tsc -b` and `npm run build` (Vite production build) both pass clean.

**Two real, pre-existing environment issues were hit (and resolved) while verifying this session's
changes — neither caused by this session's code**:

1. This sandbox originally had no Node.js at all; once installed, `npm run test` failed outright
   with `ERR_REQUIRE_ESM` (`html-encoding-sniffer@6.0.0` — the version actually pinned in the
   committed `package-lock.json` — does a CJS `require()` of `@exodus/bytes`, which has been a
   pure-ESM-only package since its first release; reproduces identically on the pre-existing
   test files, unrelated to any app code). Node 20.x hits it every time; Node ≥22.14 (where
   `require(esm)` support is more complete) avoids the crash.
2. Even with a working Node version, every vitest worker — default parallelism, and even
   `--maxWorkers=1` — timed out trying to start at all when run from this checkout's actual path
   under `/mnt/c/Users/...` (a Windows filesystem mounted into WSL2): `Timeout waiting for
   worker to respond`, 13 minutes, zero tests run. Confirmed the cause by copying the exact same
   `ui-argus` source to a native Linux path and running from there instead: **13/13 files, 65/65
   tests pass in under 2 seconds** — so this really is WSL2/`/mnt/c` I/O latency in Vitest's
   worker startup, not a Vitest, Node, or app bug. Running (or at least testing) this repo from
   a native filesystem path, not a `/mnt/c/...` one, avoids it entirely.

`npx tsc -b`, `npm run build`, and the full Vitest suite (65 tests across 13 files, including
the new `RequireRole.test.tsx`/`Access.test.tsx`/`Profile.test.tsx`/`Fleet.test.tsx` and the
role-gating additions to `Sidebar.test.tsx`) all verified passing clean in this session.

- **`src/api/`** — one client module per backend resource (`client`, `auth`, `me`, `users`,
  `trucks`, `drivers`, `routes`, `alerts`), all built on `apiFetch`. `routes.ts`/`alerts.ts` run
  every coordinate field through `normalizeCoordinates()` once, at this layer.
- **`src/types.ts`** — `Role` is now 4 members; added `RouteWithStatus` (the
  `GET /api/routes/active` shape, embedding a route's newest `Status_Route` + truck/driver
  names).
- **`src/components/RequireRole.tsx`** (new) — a route-level role gate on top of
  `ProtectedRoute`'s auth-only check, used for `/access`.
- **`src/pages/Access.tsx`** (new) — root_admin/admin user management, role-aware (see "Auth"
  above).
- **`src/pages/Profile.tsx`** (new) — self-service email/name/phone/password edit for any role.
- **`src/utils/format.ts`** — `relativeTime`/`clock`/`longDay`/`daysUntil` now default to
  `new Date()` instead of the retired `MOCK_NOW` fixture constant; the optional `now` param is
  still there purely for tests to pin a fixed reference time.

## Next steps (not started)

- Fix the `html-encoding-sniffer`/`@exodus/bytes` toolchain bug for real (pin a working
  `html-encoding-sniffer` version via `overrides`, regenerate `package-lock.json`) and document
  both the Node ≥22.14 requirement and the "run tests from a native filesystem path, not
  `/mnt/c/...` under WSL2" gotcha in the README, rather than relying on whoever hits this next
  rediscovering both workarounds from scratch.
- Extend the Vitest suite to `Drivers.tsx`/`TravelManagement.tsx` (same write-gating shape as
  the new `Fleet.test.tsx`) and to `LiveOps`/`AlertTriage`.
- A caching/data-fetching layer (TanStack Query or similar) — every screen still does its own
  `useEffect` fetch with no shared cache; not blocking, just a quality improvement.
- Everything else — OSRM for `TravelManagement`, a real-time push mechanism for `LiveOps`,
  `Alert.media_url` storage — is tracked in `INTEGRATION.md`, not here.
