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

**UI copy and route paths are in English** (`/fleet`, `/drivers`, `/routes`,
`/routes/:id/live`, `/alerts/:id`, `/alerts/history`, `/history`, `/access`, `/profile`), even
though the design canvas is in Spanish — translated on request.
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
  (`docs/criteria/`) explicitly reward justified language choices; static typing catches
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
  the map shows live truck positions, each route's recorded GPS path and a pin per alert from the
  real backend (`GET /api/live`, `GET /api/routes/{id}/track`; see "Live data" below). Full
  write-up (both this and the coordinate-adapter decision, with the Google Maps / Amazon Location
  rejection reasoning): `docs/designs/frontend-map-and-coordinates.md`.

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
  allow-list, filtered per-group before render — `Fleet`/`Drivers`/`Routes` are visible
  to `root_admin`/`admin`/`guardian` (read-only for guardian, enforced inside those pages, not
  by hiding the nav item) but not `truck_driver`; `Access` is `root_admin`/`admin` only. A
  second component, `src/components/RequireRole.tsx`, adds a route-level version of the same
  check (used to gate `/access` directly, in case someone types the URL rather than clicking
  the nav item). Creating other accounts is now a real screen too — `Access.tsx`
  (`root_admin`/`admin`, root_admin sees/creates any role, `admin` is scoped server-side to
  guardian accounts only) — no more curl/Swagger required.
- **Removed (2026-10-23)**: the inert "Keep me signed in" checkbox and the `href="#"` "Forgot
  your password?" link. Neither had anything behind it; add them back only with a real flow.

## Docker architecture

Multi-stage `Dockerfile`, mirroring `src/cv-argus`'s Docker-first pattern but simpler — this
is a plain frontend with no native-wheel/glibc-vs-musl concerns, so it uses Alpine rather than
`cv-argus`'s Debian slim base:

- **`deps`**: `npm install`, cached separately so it only re-runs when `package*.json` change.
  (`npm ci` works too since the lockfile fix described under "Current status"; switching this
  stage to it is a small open step.)
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
- **`src/test/setup.ts` polyfills `crypto.subtle`** via Node's `webcrypto` — jsdom provides
  `window.crypto` but not `.subtle`, which `sha256Hex` (and therefore the real `Login` submit
  path) needs; without this, any test exercising it throws immediately. Also clears
  `localStorage` in `afterEach` for the same leakage reason `cleanup()` is already there.
- **`src/test/fixtures.ts`** builds C3-shaped test data (`makeAlert`, `makeIncident`,
  `makeRouteLive`, `makePoint`, `makeLive`, `at(seconds)`). It is test-only; the old app
  fixtures in `src/data/` are long gone.
- **The map is mocked in page tests.** Leaflet doesn't run in jsdom, so the `LiveOps`,
  `RouteLiveView` and `RouteHistory` tests `vi.mock` `FleetMap` and assert on the props the page
  hands it (`markers`, `paths`, `pins`, `focus`). The layer builders are pure and tested in
  `utils/mapLayers.test.ts`. What Leaflet then draws (icons, polylines, `flyTo`, following) is
  only checked in a real browser.
- **What's covered (31 files, 248 tests):** the C3 logic as pure functions —
  `utils/live.test.ts` (the `/api/live` merge, `(id_route, t)` dedup including millisecond
  times, the per-route cap, dropping the track of routes that left the active set),
  `utils/incidents.test.ts` (tier order, route grouping, note/reason rules),
  `utils/position.test.ts`, `utils/mapLayers.test.ts`, `utils/status.test.ts` (labels),
  `utils/format.test.ts`; the hooks — `hooks/usePolling.test.tsx` (interval, pause while hidden,
  no stacking), `hooks/useLive.test.tsx` (the `since` cursor, request-order guard, `/track` seed
  and retry, ms timestamps); the components — `IncidentFeed`, `CloseIncidentDialog`,
  `RouteEndActions` (incl. the 503 retry), `StatusPill`, `SimulatorControl` (10 s poll),
  `Sidebar`, `RequireRole`, `RecordTable`, `SearchBox`; and the pages — `LiveOps`,
  `RouteLiveView`, `AlertHistory`, `RouteHistory`, `TravelManagement`, `Fleet`, `Access`,
  `Profile`, `Login`.
- **Not covered:** `AppLayout`, `PageHeader`, `Icon`, `Drivers` (same write-gating shape as
  `Fleet`), and Leaflet's rendering (above).
- Run locally with `npm run test` (`vitest run`, a single pass). See "Current status" for the
  Node version and the WSL2 `/mnt/c` caveat.

## Live data (contracts C3, `docs/designs/final-tweaks-contracts.md`)

The "final tweaks" rework moved the live screens from polling several per-alert endpoints to one
poll and an incident model. `types.ts` follows C3.1–C3.7 field for field (`RouteLive`,
`LivePosition`, `TrackPoint`, `LiveResponse`, `Incident`, the new `Alert` fields, `Vigilance`
with `unknown`, `IncidentState`, `TimeSource` with `relative`). Timing constants are in
`src/constants.ts` (C4): `UI_POLL_MS` 5000, `ALERT_HISTORY_POLL_MS` 5000, `SIMULATOR_POLL_MS`
10000, `LIVE_TRACK_MAX_POINTS_PER_ROUTE` 10000. The backend's own thresholds (group window,
"old" after 1 h, ...) are deliberately **not** mirrored: the UI trusts the server's `tier` and
`incident_state`.

### `/api/live` and `useLive`

- **One poll.** `hooks/useLive.ts` calls `GET /api/live` every `UI_POLL_MS` through
  `hooks/usePolling.ts`, which **pauses while the tab is hidden** (`document.visibilityState`),
  polls at once when it shows again, and never stacks a tick on a pending request. Only the
  data refreshes; the page never reloads.
- **Merge** (`utils/live.ts`, pure): `routes` and `incidents` come in full and replace the old
  ones; the server's `tier` is trusted, never recomputed between polls. `track` is a delta
  (points received since the previous `server_time`, passed back as `since`). Points are
  appended and **de-duplicated by `(id_route, t)`** on the parsed instant, so the backend's 10 s
  `LIVE_SINCE_OVERLAP` re-send and an equal time written differently (`.250Z`, `.250000Z`,
  `+00:00`) collapse, while two points in the same second with different **milliseconds** stay
  two points (C3.3 `[sync 3]`: the backend no longer rounds point times to the second). Each
  route keeps at most **10000 points**, newest kept. Without a `route` filter, the track of a
  route that left the active set is dropped.
- **Sequence guard.** Each request takes a sequence number, and a response older than the last
  one applied is dropped, so `refresh()` right after a close can't be overwritten by an older
  interval poll landing last. Changing the scope (token or route) starts over with no cursor.
- **Route view seed.** With a `route`, the whole path is seeded once from
  `GET /api/routes/{id}/track` (any status; `/api/live` without `since` only returns 15 min) and
  the live deltas fold in with the same dedup, in whichever order they arrive. A failed seed is
  retried on the next poll.

### Screens

- **Live operations** (`pages/LiveOps.tsx`, `/`): stat tiles count routes by `incident_state`.
  The map has one marker per route, **keyed by `id_route`** and coloured by `incident_state`
  (not by the newest point), a hollow dashed "last known" marker when the position is a saved
  fix, each active route's GPS path, and a pin per open-incident alert. The right panel is the
  incident feed (tier chips All/Critical/Medium/Old) or the active routes (with Complete trip /
  Stop route for root_admin/admin and a "View live" link).
- **Route view** (`pages/RouteLiveView.tsx`, `/routes/:id/live[?alert=]`, and the legacy
  `/alerts/:alertId`): `/api/live?route=` plus the `/track` seed; the map follows the truck.
  "Show on map" pauses following to fly to an alert; a **Follow truck** button on the map clears
  that focus and resumes following. Tabs: "This alert" (when focused: position, Close incident
  for the alert's open incident, notes, de-escalation) and "Incidents" (Open /
  Closed filter chips; the closed list is `GET /api/incidents?route_id=&status=closed`). A trend
  pill (improving/worsening/steady) compares the newest point's vigilance between polls;
  `unknown` breaks it.
- **Alert history** (`pages/AlertHistory.tsx`, `/alerts/history`): closed incidents
  (`GET /api/incidents?status=closed&limit=200`), polled every 5 s, newest close first, each with
  its close time, note and alerts. It has no map: each alert links to the route view, which is
  where its "Show on map" is.
- **Route history** (`pages/RouteHistory.tsx`, `/history`): completed and stopped routes, each
  expandable into its GPS path (`/track`, downsampled by the server) on a small map with a pin
  per alert, and its alerts with positions. Each alert row links to the route view focused on
  it and has a "Show on map" that flies the small map to its pin.
- **Routes** (`pages/TravelManagement.tsx`, `/routes`): Start trip, and Complete trip / Stop
  route through `RouteEndActions`. Those two are disabled while a Start trip is in flight.

### Incidents, tiers and alert cards

- `components/IncidentFeed.tsx` + `utils/incidents.ts`: tier sections **Critical → Medium →
  Older than 1 h** (the server's `tier`), incidents newest `last_alert_at` first. Inside a
  section there is one route header per route (routes ordered by their newest incident; off in
  the single-route view), with a "Route ended" pill when `route_ended`.
- `IncidentCard`: severity, alert count, time span, "Driver OK since" (informational: it never
  closes anything), the replayed badge when any alert came from the Pi's dead-letter queue
  ("Recovered after lost connection"), and, once closed, "Closed HH:MM" and the close note.
  `closed_by` is not shown: it may be `null` (closed by the system, e.g. the data migration,
  C3.4 `[sync 3]`), and the UI never assumes it resolves to a user.
- `components/AlertCard.tsx`: the `ALERT_TYPE_LABEL` text (unknown types show the raw string),
  its position via `utils/position.ts` (coordinates, "Last known (N old)", or "Position
  unknown"), "Show on map" when it has coordinates, and the replayed badge. It shows "Driver OK
  since" only standalone: `IncidentCard` passes `showDriverOk={false}`, so the pill appears
  once per incident card (on the incident header), not again on each of its alerts.

### Dialogs and the close confirmation

- **Close incident** (`components/CloseIncidentDialog.tsx`, guardian and root_admin only,
  `canCloseIncidents`): `POST /api/incidents/{id}/close` with `{note}`. A critical incident needs
  a note ("A note is required to close a critical incident", checked before sending, same as the
  backend's 422). Closing closes every alert of the incident.
- **After a successful close** (`components/CloseNotice.tsx`, on Live ops and the route view,
  with or without a focused alert): an always-mounted `role="status"` element reads
  `<Severity> incident closed · <plate> · Closed HH:MM` (`timeOfDay`: a bare Mexico City HH:MM).
  It is not auto-dismissed: the next close replaces it and navigation clears it. The incident
  leaves the Open list at once (the page hides closed ids locally, so a poll already in flight
  can't flash it back), and the view doesn't switch tabs or chips.
- **Complete trip / Stop route** (`components/RouteEndActions.tsx`, root_admin and admin only,
  hidden on an ended route), each behind a dialog. Complete is `PUT /api/routes/{id}` with only
  `operative_status: completed` and `actual_arrival`; Stop is `POST /api/routes/{id}/stop` with
  a reason ("A reason is required to stop a route"), after which the route reads "Stopped". A
  **503** (the backend's per-route lock was busy, C3.6 `[sync 3]`) always shows "Route is busy,
  try again" **inside the dialog, which stays open** with the reason kept, so pressing again
  retries. Other errors (e.g. 409 for an ended route) show the server's message. The UI never
  reopens a finished route (the backend answers 409) and never sends `resolved_at` (a 422 since
  C2.4).

### Test hooks it-argus relies on (C3.7 `[sync 3]`)

Keep these stable; `src/it-argus` selects on them:
- tier sections are `role="region"` named `Critical incidents`, `Medium incidents`,
  `Older than 1 h incidents` (the visible header adds a count, e.g. `Older than 1 h · 1`);
- the close confirmation is the `role="status"` element containing `Closed HH:MM`;
- dialogs are `role="dialog"` named `Close incident` / `Stop route` / `Complete trip`, with a
  textbox (Close, Stop), a confirm button of the same name, and the error texts above;
- each incident card is an `article` named `<Severity> incident` with its own `Close incident`
  button; the route view's `Open` / `Closed` chips are `button`s with `aria-pressed`.

### Removed: panic button and grip

The panic button and the steering-wheel grip sensor were removed from the system, and so was
their UI: no grip panel, no panic filter or badge, no grip status on alert cards. `panic_button`
stays only as a label ("Panic button"), as an `alert_type` and as a `source`, so old rows still
read. `grip_status` stays in `types.ts` because old rows carry it, and a new grip sensor is in
the works; it is never shown or requested until that lands.

### Removed: dead UI (2026-10-23)

- The route view's **"Model output" panel** (Drowsy / Not drowsy bars). cv-argus's parts
  pipeline fills `ai_metadata.scores` with a fixed value per severity (`RULE_P_DROWSY`: medium
  0.75, critical 0.95), so the bars only restated the severity as a fake probability.
  `AlertAiMetadata` no longer types `scores`/`clip_seconds`; the backend still sends them.
- The route view's **"Immediate actions"** buttons (Trigger in-cab alarm, Contact the driver,
  Notify logistics): mockup leftovers with no handler.
- Unused API wrappers `deleteDriver`, `deleteTruck`, `deleteRoute`, `getRoute`, `getIncident`,
  and `utils/format.ts`'s `pct`.

### Known limits

- **Alert history reads at most 200 closed incidents, and the server orders them by
  `last_alert_at`**, not by `closed_at`; the page re-sorts by `closed_at` on the client. With
  more than 200 closed incidents, an old incident closed just now can be missing. A
  `sort=closed_at` option on `GET /api/incidents` (backend) would fix it.
- **Legacy `low` alerts have no incident** (`id_incident: null`), so they don't appear in Alert
  history (the old `reviewed=true` list showed them). They still show in Route history.
- **Leaflet layers are only checked in a browser**: unit tests assert the layer data handed to
  `FleetMap`, not what Leaflet draws, and not `flyTo`/following.
- The route view's per-alert "Operator review" (notes) still uses `PUT /api/alerts/{id}`;
  closing is per incident.

## Current status

All screens call the real backend; `src/data/fixtures.ts` is long gone. The final-tweaks rework
(above) replaced `GET /api/routes/active` + `GET /api/alerts` polling with `/api/live`, replaced
per-alert review-to-close with incidents, and removed the panic/grip UI. `api/routes.ts` no
longer wraps `/api/routes/active` or `/api/routes/{id}/status`.

**Verified** (from a native Linux copy, Node 24): `npm ci`, `npm test` (31 files, 248 tests),
`npm run build` (`tsc -b && vite build`), and `eslint .` (one pre-existing `AuthContext.tsx`
fast-refresh warning). **Not verified here:** a real-browser click-through of the reworked
screens and the Leaflet layers. That is `src/it-argus`'s Playwright suite against the real
backend, run in the integration step.

**`package-lock.json` fix.** The lock had been committed with a machine-local root version
(`file:../../../../../../mnt/c/...`) and the same bogus prefix on every `packages` key, so
`npm ci` reported every dependency as missing. The root version is now `0.0.1`, the `""`
package carries the `package.json` name, and the keys are plain `node_modules/...` paths. No
`resolved`/`integrity` value changed. `npm ci` now installs cleanly.

**Two environment gotchas (not code bugs):**
1. **Node ≥22.14** (Node 24 used here). On Node 20, `npm run test` crashes with
   `ERR_REQUIRE_ESM` (`html-encoding-sniffer` does a CJS `require()` of the ESM-only
   `@exodus/bytes`). ESLint, `tsc` and the build are unaffected.
2. **WSL2 `/mnt/c` paths:** Vitest workers time out starting (`Timeout waiting for worker to
   respond`) when run from a Windows-mounted checkout. Copy the module to a native Linux path
   (e.g. `rsync -a --exclude node_modules --exclude dist src/ui-argus/ ~/ui-run/`) and test there.

### History (short)

Earlier iterations, superseded where they conflict with the sections above:
- `RouteHistory.tsx` replaced the "Trip history" placeholder; `AlertTriage.tsx` became
  `RouteLiveView.tsx` with two URL shapes; `AlertHistory.tsx` was first a `reviewed=true` alert
  list (now closed incidents); `FilterChips.tsx` was extracted for the pill-row filters.
- `FleetMap`'s `follow`: `MapContainer`'s `bounds` are init-only (react-leaflet builds the
  Leaflet map once, before the route view's position has loaded), so `FollowMarker` does the
  framing itself: `setView` on first appearance, `flyTo` at the current zoom afterwards.
- `components/SimulatorControl.tsx`: root_admin-only On/Off for the AWS on-demand simulator
  (fixed 10-minute sessions, 12 a month, no duration input), polling every 10 s while open.
- `components/DeviceCredentials.tsx`: Fleet's Generate / Rotate / Revoke for a truck's device
  key, shown once as `argus-device.env`.
- Start trip (`TravelManagement.tsx`): a truck's device only gets a route while it's in
  progress, so Start is what moves a real device off `no_route`; it's refused while the truck
  already has an active trip.
- Severity `low` reads "Normal"/green everywhere (a fine truck isn't a severity).

## Next steps (not started)

- Switch the Dockerfile's `deps` stage to `npm ci` (the lockfile works now).
- Ask the backend for `GET /api/incidents?sort=closed_at` if Alert history ever outgrows 200
  closed incidents, and decide whether legacy `low` alerts need a view.
- Fix the `html-encoding-sniffer`/`@exodus/bytes` Node 20 crash for real (an `overrides` pin),
  or state Node ≥22.14 in `package.json` `engines`.
- A `Drivers.test.tsx` (same write-gating shape as `Fleet.test.tsx`).
- A caching/data-fetching layer (TanStack Query or similar) for the non-live screens.
- Everything else — OSRM for `TravelManagement`, a push channel instead of the 5 s poll,
  `Alert.media_url` storage — is tracked in `INTEGRATION.md`.
