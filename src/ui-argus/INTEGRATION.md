# INTEGRATION.md — backend connectivity: what's missing and where it plugs in

A map of every point in `ui-argus` where backend code needs to land, for whoever wires this
frontend up to `src/backend-argus` (the FastAPI backend — now real code, not just a plan). All
of this is still genuinely missing on the frontend side, despite the backend existing — this
is a checklist, not a status report on work in progress here.

**As of the screen port:** every screen below is a real component rendering fake data from
`src/data/fixtures.ts` (shapes in `src/types.ts`, now reconciled field-for-field with
`src/backend-argus`'s Pydantic schemas — see that module's `CLAUDE.md` for the old ER-diagram
names vs. the corrected ones actually implemented). The "Currently" column reflects the fixture
state. Wiring a screen up = swap its `fixtures` import for an `src/api/*` call and delete the
local-state mutation; the component structure stays. Endpoints referenced below are
`src/backend-argus`'s real router paths (see that module's `CLAUDE.md`'s "Routes per resource");
nothing here invents new ones except where explicitly flagged as a gap.

## Cross-cutting gaps (touch every screen, not just one)

These aren't per-page — they're infrastructure every page below depends on, and none of it
exists yet:

1. **~~No API client~~ — resolved for login, the pattern exists now.** `src/api/client.ts`
   (`apiFetch`, base URL from `VITE_API_BASE_URL`, `Authorization: Bearer` support, FastAPI
   `detail` error normalization, a `setUnauthorizedHandler` hook for session-expiry) and
   `src/api/auth.ts` (`login()`) are real, and every other resource follows the same pattern —
   `src/api/trucks.ts`, `src/api/alerts.ts`, etc. still don't exist. Whether to add a
   caching/data-fetching layer on top (TanStack Query is the common choice — it would materially
   simplify the loading/error/refetch handling every table screen below needs) is still an open
   decision, not made here. **Coordinate fields must be normalised at this boundary**: every
   `Alert.coordinates`, `Status_Route.current_coordinates` and `Route.destination_coordinates`
   from the API goes through `normalizeCoordinates()` in `src/utils/geo.ts` before it reaches a
   component. It already handles `{lat,lon}` / `{lat,lng}` / `"lat,lon"` / bare GeoJSON position
   / GeoJSON `Point` — MongoDB's `2dsphere` index stores points as GeoJSON
   `{ type:'Point', coordinates:[lng,lat] }` (longitude first), which is neither the `{lat,lon}`
   shape `src/types.ts` declares nor the `[lat,lng]` order Leaflet wants, so this conversion is
   not optional. Rationale: `docs/designs/frontend-map-and-coordinates.md`.
2. **Resolved.** `src/types.ts` models `User`, `Truck`, `Driver`, `Route`, `Status_Route`,
   `Alert`, and now `LoginUser`/`LoginResponse` too, mirroring `src/backend-argus`'s Pydantic
   schemas field-for-field — see that module's `CLAUDE.md` for the full table.
3. **~~No auth/session state~~ — resolved.** `src/context/AuthContext.tsx` (`AuthProvider` +
   `useAuth()`) holds `{ token, user }` in `localStorage` (key `argus.session`) and is the single
   source of truth for the logged-in user — `Sidebar.tsx`'s footer reads it instead of the
   retired `CURRENT_USER` fixture.
4. **~~No route guarding~~ — resolved for authentication, not yet for role.**
   `src/components/ProtectedRoute.tsx` gates the whole `AppLayout` route tree in `App.tsx`
   behind a session (redirecting to `/login`, and back to the originally-requested page on
   success). What's still open: a **role** check on top of that — `src/types.ts`'s `Role`
   matches `src/backend-argus`'s `Role` enum exactly (`root_admin` / `guardian` /
   `truck_driver`), so the role list itself isn't an open question, but `Sidebar.tsx` still
   renders both nav-groups unconditionally regardless of the logged-in user's role — there's no
   confirmed spec yet for which items each role should see, so this pass deliberately didn't
   guess at one.
5. **No real-time strategy decided.** The Control Tower dashboard's mockup shows a live
   alert feed and live truck positions ("EN VIVO"). Polling `GET /api/alerts` on an interval
   is the simplest option; a WebSocket/SSE push is the more genuine real-time fit and the one
   that would actually exercise this project's "Sistemas Distribuidos" grading requirement
   (see the top-level `CLAUDE.md`) — but nothing in the committed API list
   (`semantic-design.drawio.xml`) specifies either yet. This needs a decision made with
   whoever builds the backend, not assumed unilaterally on the frontend side. The map itself
   now exists (`src/components/FleetMap.tsx`, `react-leaflet`, fixture-fed) — so the remaining
   work here is the data source and refresh mechanism, not the map. Note `react-leaflet`'s
   `MapContainer` `center`/`zoom`/`bounds` are init-only; `Marker` `position` *is* reactive,
   so live positions move markers for free, but a `useMap()` child effect is needed to re-fit
   the viewport when the fleet moves.
6. **CORS** — resolved: `src/backend-argus`'s `CORSMiddleware` allows `http://localhost:5173`
   (the Vite dev server origin) by default (`CORS_ORIGINS` config var), so this no longer needs
   separate follow-up once the fetches below are wired up.

## Per-screen breakdown

| Screen (file) | Endpoint(s) | Currently | Missing |
|---|---|---|---|
| `src/pages/Login.tsx` | `POST /api/auth/login` | **Done.** Real submit handler (SHA-256 pre-hash, see `CLAUDE.md`'s "Auth"), inline error display, on success stores the session and redirects back to the originally-requested page (or `/`) | Redirect-by-*role* specifically has nothing to redirect to yet — there's only one post-login destination (`/`), not separate per-role landing pages |
| `src/App.tsx` (routing shell) | — | **Done.** `ProtectedRoute` gates the whole `AppLayout` tree behind a session; `/login` stays outside it | Role-based route restrictions (not just "logged in or not") once individual screens need them |
| `src/components/Sidebar.tsx` | — | Footer now reads the logged-in user's name/initials/role from `AuthContext`; sign-out clears the session | Still shows **both** role nav-groups unconditionally — hiding the one a role can't see needs a confirmed spec first, not guessed here |
| `src/pages/LiveOps.tsx` | `GET /api/routes/active` | Stat tiles / alert feed computed from fixtures; a real `react-leaflet` map (`FleetMap`) with one truck marker per `statusRoutes[]` row, positioned from `current_coordinates`; feed severity filter works; rows link to `/alerts/:id` | Fetching + the real-time strategy from gap #5 (feed `FleetMap` markers from live data; add a `useMap()` child effect to re-fit bounds as the fleet moves). **The "no endpoint lists all active routes" gap this row used to flag is now resolved**: `src/backend-argus` added `GET /api/routes/active`, returning in-progress routes each embedded with their latest status + truck/driver refs specifically for this screen — see that module's `CLAUDE.md` for why it's a dedicated endpoint rather than `?status=active` |
| `src/pages/AlertTriage.tsx` | `GET /api/alerts/:id`, `PUT /api/alerts/:id` | Looks the alert up in fixtures by `:alertId` (`useParams`); "not found" state; review checkbox + notes are local state, "Save" flips a local flag | Fetch on mount; `PUT` `reviewed_by_operator`/`operator_notes` from the checkbox + textarea; **also unresolved**: `Alert.media_url` — how/where captured clips are stored and served (S3? the backend directly?) isn't decided anywhere yet, so the media placeholder has nothing real to point at |
| `src/pages/Fleet.tsx` | `GET/POST/PUT/DELETE /api/trucks` | Table from fixtures with client-side search; row-select → edit panel; add/edit mutate a local `useState` copy | Swap the fixture import for a fetch; point the panel's submit at `POST`/`PUT`, add a delete affordance |
| `src/pages/Drivers.tsx` | `GET/POST/PUT/DELETE /api/drivers` | Same shape as Fleet | Same as Fleet |
| `src/pages/TravelManagement.tsx` | `GET/POST/PUT/DELETE /api/routes` | Route table from fixtures with search; "New route" form creates a `scheduled` row in local state | Fetch + real `POST`; the "computed with OSRM on confirm" note means the create submit calls OSRM (directly or backend-proxied — not decided) for `destination_coordinates`/`estimated_arrival` before saving — currently stubbed to `{lat:0,lon:0}` / `null` |

## Explicitly not in scope yet

Carried over from the earlier UI prioritization (see the top-level `CLAUDE.md` and the design
canvas) — no page, route, or mockup exists for these, so there's nothing to wire up:

- **Access Panel (`/api/users`)** — planned, root-only, not built.
- **Reports Panel** — cut; no committed API/table effort behind it yet.
- **Geofence management** — in the ER model, never appeared in the committed API list at all.

If any of these get prioritized later, this doc should grow a row for them rather than the
work happening undocumented.
