# INTEGRATION.md — backend connectivity: what's wired and what's still open

A map of every point in `ui-argus` where backend code lands, for whoever touches this frontend
next. Originally written when every screen still rendered `src/data/fixtures.ts`; that fixture
file is now deleted and every screen fetches the real `src/backend-argus` API. This doc now
tracks what's genuinely still open, not a checklist of what's missing.

## Cross-cutting gaps — status

1. **Resolved.** `src/api/client.ts` (`apiFetch`) plus one module per resource:
   `src/api/{auth,me,users,trucks,drivers,routes,alerts}.ts`, all following the same pattern
   (`token` param, `ApiError` on non-2xx). **Still open**: whether to add a caching/data-fetching
   layer on top (TanStack Query) — every screen still does its own `useEffect` fetch into local
   `useState`, no shared cache. **Coordinate normalization**: `routes.ts`/`alerts.ts` run every
   `Route.destination_coordinates`/`Status_Route.current_coordinates`/`Alert.coordinates`
   response through `normalizeCoordinates()` (`src/utils/geo.ts`) once, at the API-client layer,
   per that file's own documented contract — components never see the raw wire shape.
2. **Resolved.** `src/types.ts` mirrors `src/backend-argus`'s Pydantic schemas field-for-field,
   including `RouteWithStatus` (the `GET /api/routes/active` shape) and the 4-role `Role` union.
3. **Resolved.** `src/context/AuthContext.tsx` — session in `localStorage`, plus
   `updateSessionUser()` so `Profile.tsx`'s self-edit reflects into the sidebar footer without a
   re-login.
4. **Resolved**, for both authentication and role. `src/components/ProtectedRoute.tsx` gates on
   session; a new `src/components/RequireRole.tsx` adds a narrower per-route role gate (used for
   `/access`). `src/components/Sidebar.tsx`'s `NavItem`s each carry an optional `roles` allow-list
   and are filtered before render — see the per-screen table below for exactly who sees what.
5. **Still open — polling, not push.** `LiveOps.tsx` polls `GET /api/routes/active` +
   `GET /api/alerts` every 7s (`POLL_MS`). A WebSocket/SSE push would be the more genuine
   real-time fit (and the one that exercises this project's "Sistemas Distribuidos" grading
   requirement) but nothing in the committed API list specifies one yet — unchanged from before,
   still a decision for whoever owns the backend next.
6. **Resolved.** CORS already allowed the dev origin; no follow-up needed once fetches landed.

## Per-screen breakdown

| Screen (file) | Endpoint(s) | Status |
|---|---|---|
| `src/pages/Login.tsx` | `POST /api/auth/login` | Done (unchanged from before this pass). |
| `src/pages/Profile.tsx` (**new**) | `GET`/`PUT /api/auth/me` | Done. Self-service email/name/phone/password edit for any authenticated role. No role/is_active control anywhere on this page — the backend schema doesn't even accept those fields on this endpoint. |
| `src/pages/Access.tsx` (**new**) | `GET/POST/PUT/DELETE /api/users` | Done, role-aware. `root_admin` sees/manages every account; an `admin` session only ever sees/creates/edits **guardian** accounts (mirrors the backend's own scoping — the role `<select>` is locked to `guardian` for an admin actor). "Deactivate" (`PUT is_active:false`) and "Delete permanently" (`DELETE`, a real hard delete) are two distinct, separately-labeled buttons. Route-gated to `root_admin`/`admin` via `RequireRole`. |
| `src/App.tsx` / `src/components/Sidebar.tsx` | — | Done. `/access` (root_admin/admin) and `/profile` (everyone) routes added; nav items filtered by role — `Fleet`/`Drivers`/`Routes` visible to `root_admin`/`admin`/`guardian` (guardian's copy is read-only, enforced inside those pages — see below), hidden from `truck_driver`; `Access` visible only to `root_admin`/`admin`. |
| `src/pages/LiveOps.tsx` | `GET /api/routes/active`, `GET /api/alerts` | Done. Polls both every 7s; markers/stat tiles come from `RouteWithStatus.latest_status`; alert feed resolves truck/driver names from active routes first, falling back to a one-time `GET /api/drivers`/`GET /api/trucks` fetch for alerts on routes that are no longer active. |
| `src/pages/AlertTriage.tsx` | `GET /api/alerts/:id`, `GET /api/routes/:id`, `PUT /api/alerts/:id` | Done. Loading and "not found" are now genuinely distinct states. Review checkbox/notes/Save are `root_admin`/`guardian` only (matches `review_alert`'s real RBAC) — `admin`/`truck_driver` see a read-only summary instead. **Still unresolved**: `Alert.media_url` storage/serving (S3? the backend directly?) isn't decided anywhere, so the media placeholder still has nothing real to point at. |
| `src/pages/Fleet.tsx` | `GET/POST/PUT /api/trucks` | Done. Write access (`Add truck` + edit Save) is `root_admin`/`admin` only; a `guardian` still sees the table and can open a row, but the panel renders read-only (disabled inputs, no Save). No delete affordance in the UI yet even though `DELETE /api/trucks/:id` exists server-side — not needed for the current workflow. |
| `src/pages/Drivers.tsx` | `GET/POST/PUT /api/drivers` | Same shape and same gating as Fleet. |
| `src/pages/TravelManagement.tsx` | `GET/POST /api/routes` | Done for create + list; write access (the "New route" panel) is `root_admin`/`admin` only, hidden entirely for `guardian`. **Still stubbed**: `destination_coordinates`/`estimated_arrival` are hardcoded (`{lat:0,lon:0}` / `null`) pending OSRM integration — unchanged from before, still out of scope for this pass. No edit/delete UI yet. |

## Explicitly not in scope yet

- **Reports Panel** — cut; no committed API/table effort behind it yet.
- **Geofence management** — in the ER model, never appeared in the committed API list at all.
- **OSRM integration** — `TravelManagement.tsx`'s create form still stubs
  `destination_coordinates`/`estimated_arrival`; per the top-level `CLAUDE.md`, OSRM itself isn't
  part of any Docker Compose stack yet either.
- **A real-time push mechanism** for `LiveOps.tsx` (see cross-cutting gap #5).

If any of these get prioritized later, this doc should grow a row for them rather than the work
happening undocumented.
