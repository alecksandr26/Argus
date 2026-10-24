import { apiFetch } from './client'
import { normalizeCoordinates } from '../utils/geo'
import type { Route, RouteTrack } from '../types'

/** What a create/edit sends. The C3.1 fields are server-owned: `incident_state`/
 * `last_not_ok_at` come from ingest, `stop_*` only from `stopRoute()`. */
export type RouteInput = Omit<
  Route,
  | 'id_route'
  | 'created_at'
  | 'updated_at'
  | 'actual_departure'
  | 'actual_arrival'
  | 'incident_state'
  | 'last_not_ok_at'
  | 'stop_reason'
  | 'stopped_by'
  | 'stopped_at'
>

// `normalizeCoordinates` runs once here, at the API boundary, per `utils/geo.ts`'s own
// contract ("call it once, in the API client, on every coordinate field") — components never
// need to think about the raw wire shape.
function normalizeRoute(r: Route): Route {
  return { ...r, destination_coordinates: normalizeCoordinates(r.destination_coordinates) }
}

export function listRoutes(token: string, status?: Route['operative_status']): Promise<Route[]> {
  const qs = status ? `?status=${status}` : ''
  return apiFetch<Route[]>(`/api/routes${qs}`, { token }).then((rs) => rs.map(normalizeRoute))
}

// `GET /api/routes/active` and `GET /api/routes/{id}/status` are no longer called: Live
// operations and the route view read `GET /api/live` (`api/live.ts`) instead (C3.6).


export function createRoute(input: RouteInput, token: string): Promise<Route> {
  return apiFetch<Route>('/api/routes', { method: 'POST', body: input, token }).then(
    normalizeRoute,
  )
}

export function updateRoute(
  id: string,
  input: Partial<RouteInput & Pick<Route, 'actual_departure' | 'actual_arrival'>>,
  token: string,
): Promise<Route> {
  return apiFetch<Route>(`/api/routes/${id}`, { method: 'PUT', body: input, token }).then(
    normalizeRoute,
  )
}

/** `POST /api/routes/{id}/stop` ("Stop route") — root_admin + admin. `reason` is required
 * (1..500 chars after trimming; check it first with `stopReasonError` from
 * `utils/incidents.ts`). Allowed from `scheduled`/`in_progress`/`in_progress_alert`; 409 once
 * the route is completed/cancelled. The route becomes `cancelled`; its open incidents stay open,
 * and the backend rejects everything the truck's device sends for it from then on. */
export function stopRoute(id: string, reason: string, token: string): Promise<Route> {
  return apiFetch<Route>(`/api/routes/${id}/stop`, {
    method: 'POST',
    body: { reason },
    token,
  }).then(normalizeRoute)
}

/** `GET /api/routes/{id}/track` — a route's whole GPS path (any status), `t` ascending,
 * downsampled by the server past `maxPoints` (1..10000, server default 2000). */
export function getRouteTrack(id: string, token: string, maxPoints?: number): Promise<RouteTrack> {
  const qs = maxPoints !== undefined ? `?max_points=${maxPoints}` : ''
  return apiFetch<RouteTrack>(`/api/routes/${id}/track${qs}`, { token })
}
