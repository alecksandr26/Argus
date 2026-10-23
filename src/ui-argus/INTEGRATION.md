# INTEGRATION.md — backend connectivity: what's wired and what's still open

A map of every point in `ui-argus` where backend code lands, for whoever touches this frontend
next. Every screen fetches the real `src/backend-argus` API (the old `src/data/fixtures.ts` is
gone). The live screens follow the frozen contracts in `docs/designs/final-tweaks-contracts.md`
(C3: `/api/live`, incidents, positions, stop route); `CLAUDE.md`'s "Live data" section explains
how the UI uses them.

## Cross-cutting gaps — status

1. **Resolved.** `src/api/client.ts` (`apiFetch`) plus one module per resource:
   `src/api/{auth,me,users,trucks,drivers,routes,alerts,incidents,live,simulator}.ts`, all
   following the same pattern (`token` param, `ApiError` with the HTTP status on non-2xx).
   **Coordinate normalization**: `routes.ts`/`alerts.ts`/`incidents.ts`/`live.ts` run every
   coordinate field (route destinations, alert positions, including the alerts embedded in each
   incident) through `normalizeCoordinates()` (`src/utils/geo.ts`) once, at the API-client
   layer, so components never see the raw wire shape. Alert `coordinates` may be `null`
   ("Position unknown"). **Still open**: a caching/data-fetching layer (TanStack Query) for the
   non-live screens, which each still do their own `useEffect` fetch.
2. **Resolved.** `src/types.ts` mirrors the backend schemas field for field, following C3.1–C3.7:
   `RouteLive`/`LivePosition`/`TrackPoint`/`LiveResponse`, `Incident`, the new `Alert` fields
   (`id_incident`, `delivery`, `driver_ok_at` replacing `resolved_at`, nullable `coordinates`,
   `location_is_last_known`, ...), `Vigilance` with `unknown`, and the 4-role `Role` union.
3. **Resolved.** `src/context/AuthContext.tsx` — session in `localStorage`, plus
   `updateSessionUser()` so `Profile.tsx`'s self-edit reflects into the sidebar footer without a
   re-login.
4. **Resolved**, for both authentication and role. `src/components/ProtectedRoute.tsx` gates on
   session; `src/components/RequireRole.tsx` adds a narrower per-route role gate (used for
   `/access`). `src/components/Sidebar.tsx`'s `NavItem`s each carry an optional `roles`
   allow-list. Action buttons follow the backend's RBAC: Close incident for guardian and
   root_admin, Complete trip / Stop route for root_admin and admin.
5. **Still open — polling, not push.** Live operations and the route view poll `GET /api/live`
   every 5 s (`UI_POLL_MS`, paused while the tab is hidden), with a `since` cursor so only new
   GPS points come back. A WebSocket/SSE push would be the more genuine real-time fit, but no
   committed API specifies one.
6. **Resolved.** CORS allows the dev origin; on AWS the UI and `/api/*` share one CloudFront
   origin, so there is no CORS at all.

## Per-screen breakdown

| Screen (file) | Endpoint(s) | Status |
|---|---|---|
| `src/pages/Login.tsx` | `POST /api/auth/login` | Done. |
| `src/pages/Profile.tsx` | `GET`/`PUT /api/auth/me` | Done. Self-service email/name/phone/password edit for any role; never sends `role`/`is_active`. |
| `src/pages/Access.tsx` | `GET/POST/PUT/DELETE /api/users` | Done, role-aware: `root_admin` manages every account, `admin` only guardian accounts (mirrors the backend's scoping). Route-gated to `root_admin`/`admin` via `RequireRole`. |
| `src/App.tsx` / `src/components/Sidebar.tsx` | — | Done. Nav items filtered by role; `/access` root_admin/admin only. |
| `src/pages/LiveOps.tsx` | `GET /api/live`, `POST /api/incidents/{id}/close`, `PUT /api/routes/{id}` (Complete trip), `POST /api/routes/{id}/stop`, `GET /api/simulator` + `POST /api/simulator/start`, `/stop` (root_admin) | Done. One 5 s poll gives the active routes with their newest position, every open incident in full, and the GPS points since the last poll (merged and de-duplicated by `(id_route, t)`, millisecond times included). Tiered incident feed; a `role="status"` "Closed HH:MM" confirmation after a close. |
| `src/pages/RouteLiveView.tsx` (`/routes/:id/live`, `/alerts/:id`) | `GET /api/live?route=`, `GET /api/routes/{id}/track` (once), `GET /api/incidents?route_id=&status=closed`, `GET /api/alerts/{id}`, `PUT /api/alerts/{id}` (notes), `POST /api/alerts/{id}/de-escalate`, close / complete / stop as above | Done. Whole path for any route status. **Still unresolved**: `Alert.media_url` storage/serving isn't decided anywhere. |
| `src/pages/AlertHistory.tsx` | `GET /api/incidents?status=closed&limit=200`, `GET /api/routes` | Done. Polled every 5 s. **Limits**: the server orders by `last_alert_at`, so past 200 closed incidents a recently closed old one can be missing (a `sort=closed_at` option would fix it); legacy `low` alerts have no incident and don't appear here. |
| `src/pages/RouteHistory.tsx` | `GET /api/routes?status=completed`, `?status=cancelled`, `GET /api/alerts`, `GET /api/routes/{id}/track` (on expand) | Done. GPS path + alert pins on a small map, alerts with positions and "Show on map". |
| `src/pages/TravelManagement.tsx` | `GET/POST /api/routes`, `PUT /api/routes/{id}` (Start / Complete trip), `POST /api/routes/{id}/stop` | Done. Stop/Complete answer 503 "Route is busy, try again" under the backend's route lock: shown inside the dialog, which stays open for a retry. A finished route is never reopened (the backend would 409). **Still stubbed**: `destination_coordinates`/`estimated_arrival` (`{lat:0,lon:0}` / `null`) pending OSRM. |
| `src/pages/Fleet.tsx` | `GET/POST/PUT /api/trucks`, `POST /api/trucks/{id}/rotate-key`, `DELETE /api/trucks/{id}/device-key` | Done. Write access root_admin/admin; guardian read-only. Device credentials (`argus-device.env`) issued here. |
| `src/pages/Drivers.tsx` | `GET/POST/PUT /api/drivers` | Same shape and gating as Fleet. |

**Not called any more**: `GET /api/routes/active` and `GET /api/routes/{id}/status` (kept by the
backend, replaced by `/api/live`), and the per-alert "close by review" (closing is
`POST /api/incidents/{id}/close`). The UI never sends `resolved_at` (a 422 since C2.4).

## Explicitly not in scope yet

- **Reports Panel** — cut; no committed API/table effort behind it yet.
- **Geofence management** — in the ER model, never in the committed API list.
- **OSRM integration — a future iteration.** `TravelManagement.tsx`'s create form still stubs
  `destination_coordinates`/`estimated_arrival`; OSRM isn't part of any backend/UI Compose stack
  (the simulator's private OSRM is separate).
- **A real-time push mechanism** for the live screens (see cross-cutting gap #5).
- **"Missing alerts"** (events replayed late from the truck's dead-letter queue) are shown with
  the "Recovered after lost connection" badge inside their incident; a dedicated history view
  for them is part of the root `CLAUDE.md`'s pending "Missing alerts" grooming, not built here.

If any of these get prioritized later, this doc should grow a row for them rather than the work
happening undocumented.
