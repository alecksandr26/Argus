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

1. **No API client.** There's no `fetch` wrapper, no `axios` instance, no generated client —
   `VITE_API_BASE_URL` is defined in `.env.example` but nothing reads it yet. Whatever gets
   built should probably live under a new `src/api/` (e.g. `src/api/client.ts` for the base
   request wrapper, one file per resource — `src/api/trucks.ts`, `src/api/alerts.ts`, etc.).
   Whether to add a caching/data-fetching layer on top (TanStack Query is the common choice —
   it would materially simplify the loading/error/refetch handling every table screen below
   needs) is an open decision, not made here. **Coordinate fields must be normalised at this
   boundary**: every `Alert.coordinates`, `Status_Route.current_coordinates` and
   `Route.destination_coordinates` from the API goes through `normalizeCoordinates()` in
   `src/utils/geo.ts` before it reaches a component. It already handles `{lat,lon}` /
   `{lat,lng}` / `"lat,lon"` / bare GeoJSON position / GeoJSON `Point` — MongoDB's `2dsphere`
   index stores points as GeoJSON `{ type:'Point', coordinates:[lng,lat] }` (longitude first),
   which is neither the `{lat,lon}` shape `src/types.ts` declares nor the `[lat,lng]` order
   Leaflet wants, so this conversion is not optional. Rationale:
   `docs/designs/frontend-map-and-coordinates.md`.
2. **No TypeScript types for the API shapes.** Nothing in `src/` models `User`, `Truck`,
   `Driver`, `Route`, `Status_Route`, or `Alert` yet. These should mirror the backend's Pydantic
   models once they exist (see the top-level `CLAUDE.md`'s ER model reference,
   `docs/designs/ER-model.drawio.xml`) rather than being hand-guessed independently — the
   entity/field names there are the source of truth to copy field names from.
3. **No auth/session state.** No `AuthContext`, no token storage, nothing reads or writes a
   session anywhere. This blocks everything below marked "needs auth" — there is no logged-in
   user object anywhere in the app right now, not even a hardcoded stand-in.
4. **No route guarding.** `src/App.tsx` currently makes every route public — `/fleet` is
   reachable without logging in. Add a `ProtectedRoute` wrapper (redirect to `/login` when
   unauthenticated) once #3 exists, and a role check on top of it — `src/types.ts`'s `Role`
   now matches `src/backend-argus`'s `Role` enum exactly (`root_admin` / `guardian` /
   `truck_driver`, see that module's `CLAUDE.md`), so the role list itself is no longer an
   open question, only the guarding logic is unbuilt.
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
| `src/pages/Login.tsx` | `POST /api/auth/login` | Controlled form; submit routes to `/` with no auth | Real submit handler, error display, on success: store session (#3 above) and redirect by role |
| `src/App.tsx` (routing shell) | — | Every route public, no session read; `AppLayout` layout route wraps the in-app screens | `ProtectedRoute` wrapper + role-based redirect after login (#4 above) |
| `src/components/Sidebar.tsx` | — | Ported; shows **both** role nav-groups and fills the footer from the `CURRENT_USER` fixture | Read the logged-in user's name/initials/role from session state; hide the nav-group the role can't see |
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
