# INTEGRATION.md — backend connectivity: what's missing and where it plugs in

A map of every point in `ui-argus` where backend code needs to land, for whoever builds the
FastAPI backend (or comes back to wire this frontend up to it). Nothing described as
"missing" here exists yet — this is a checklist, not a status report on work in progress.

**As of the screen port:** every screen below is a real component rendering fake data from
`src/data/fixtures.ts` (shapes in `src/types.ts`, field names copied from the ER model). The
"Currently" column reflects that. Wiring a screen up = swap its `fixtures` import for an
`src/api/*` call and delete the local-state mutation; the component structure stays.
Endpoints referenced are the list already committed in
`docs/designs/semantic-design.drawio.xml`; nothing here invents new ones except where
explicitly flagged as a gap in that list.

## Cross-cutting gaps (touch every screen, not just one)

These aren't per-page — they're infrastructure every page below depends on, and none of it
exists yet:

1. **No API client.** There's no `fetch` wrapper, no `axios` instance, no generated client —
   `VITE_API_BASE_URL` is defined in `.env.example` but nothing reads it yet. Whatever gets
   built should probably live under a new `src/api/` (e.g. `src/api/client.ts` for the base
   request wrapper, one file per resource — `src/api/trucks.ts`, `src/api/alerts.ts`, etc.).
   Whether to add a caching/data-fetching layer on top (TanStack Query is the common choice —
   it would materially simplify the loading/error/refetch handling every table screen below
   needs) is an open decision, not made here.
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
   unauthenticated) once #3 exists, and a role check on top of it once the backend's `role`
   values are known (the ER model's `User.role` field doesn't enumerate its possible values
   anywhere yet — confirm them with whoever builds `/api/users` before hardcoding a role list
   in the frontend).
5. **No real-time strategy decided.** The Control Tower dashboard's mockup shows a live
   alert feed and live truck positions ("EN VIVO"). Polling `GET /api/alerts` on an interval
   is the simplest option; a WebSocket/SSE push is the more genuine real-time fit and the one
   that would actually exercise this project's "Sistemas Distribuidos" grading requirement
   (see the top-level `CLAUDE.md`) — but nothing in the committed API list
   (`semantic-design.drawio.xml`) specifies either yet. This needs a decision made with
   whoever builds the backend, not assumed unilaterally on the frontend side.
6. **CORS isn't the frontend's code, but will silently break this if forgotten**: the FastAPI
   backend will need to allow the Vite dev server's origin (`http://localhost:5173`) once any
   of the fetches below are wired up.

## Per-screen breakdown

| Screen (file) | Endpoint(s) | Currently | Missing |
|---|---|---|---|
| `src/pages/Login.tsx` | `POST /api/auth/login` | Controlled form; submit routes to `/` with no auth | Real submit handler, error display, on success: store session (#3 above) and redirect by role |
| `src/App.tsx` (routing shell) | — | Every route public, no session read; `AppLayout` layout route wraps the in-app screens | `ProtectedRoute` wrapper + role-based redirect after login (#4 above) |
| `src/components/Sidebar.tsx` | — | Ported; shows **both** role nav-groups and fills the footer from the `CURRENT_USER` fixture | Read the logged-in user's name/initials/role from session state; hide the nav-group the role can't see |
| `src/pages/LiveOps.tsx` | `GET /api/alerts`, `GET /api/routes/:id/status` | Stat tiles / map markers / alert feed all computed from fixtures; feed severity filter works; rows link to `/alerts/:id` | Fetching + the real-time strategy from gap #5; **also a real gap in the committed API list itself**: there's no endpoint to list *all currently-active* routes/trucks at once, only `/api/routes/:id/status` for one route at a time — the dashboard's fleet-wide map and stat tiles need something like `GET /api/routes?status=active`, which doesn't exist in `semantic-design.drawio.xml` yet and should be raised with the backend, not assumed into existence here |
| `src/pages/AlertTriage.tsx` | `GET /api/alerts/:id`, `PUT /api/alerts/:id` | Looks the alert up in fixtures by `:alertId` (`useParams`); "not found" state; review checkbox + notes are local state, "Save" flips a local flag | Fetch on mount; `PUT` `reviwed_by_operator`/`operator_notes` from the checkbox + textarea; **also unresolved**: `Alert.media_url` — how/where captured clips are stored and served (S3? the backend directly?) isn't decided anywhere yet, so the media placeholder has nothing real to point at |
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
