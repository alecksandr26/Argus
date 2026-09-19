import { apiFetch } from './client'
import { normalizeCoordinates } from '../utils/geo'
import type { Route, RouteWithStatus, StatusRoute } from '../types'

export type RouteInput = Omit<
  Route,
  'id_route' | 'created_at' | 'updated_at' | 'actual_departure' | 'actual_arrival'
>

// `normalizeCoordinates` runs once here, at the API boundary, per `utils/geo.ts`'s own
// contract ("call it once, in the API client, on every coordinate field") — components never
// need to think about the raw wire shape.
function normalizeRoute(r: Route): Route {
  return { ...r, destination_coordinates: normalizeCoordinates(r.destination_coordinates) }
}

function normalizeStatus(s: StatusRoute): StatusRoute {
  return { ...s, current_coordinates: normalizeCoordinates(s.current_coordinates) }
}

function normalizeRouteWithStatus(r: RouteWithStatus): RouteWithStatus {
  return {
    ...normalizeRoute(r),
    latest_status: r.latest_status ? normalizeStatus(r.latest_status) : null,
    truck_plate_number: r.truck_plate_number,
    driver_full_name: r.driver_full_name,
  }
}

export function listRoutes(token: string, status?: Route['operative_status']): Promise<Route[]> {
  const qs = status ? `?status=${status}` : ''
  return apiFetch<Route[]>(`/api/routes${qs}`, { token }).then((rs) => rs.map(normalizeRoute))
}

/** `GET /api/routes/active` — in-progress routes embedding their newest status snapshot,
 * built specifically for `LiveOps.tsx`. */
export function listActiveRoutes(token: string): Promise<RouteWithStatus[]> {
  return apiFetch<RouteWithStatus[]>('/api/routes/active', { token }).then((rs) =>
    rs.map(normalizeRouteWithStatus),
  )
}

export function getRoute(id: string, token: string): Promise<Route> {
  return apiFetch<Route>(`/api/routes/${id}`, { token }).then(normalizeRoute)
}

export function createRoute(input: RouteInput, token: string): Promise<Route> {
  return apiFetch<Route>('/api/routes', { method: 'POST', body: input, token }).then(
    normalizeRoute,
  )
}

export function updateRoute(
  id: string,
  input: Partial<RouteInput>,
  token: string,
): Promise<Route> {
  return apiFetch<Route>(`/api/routes/${id}`, { method: 'PUT', body: input, token }).then(
    normalizeRoute,
  )
}

export function deleteRoute(id: string, token: string): Promise<{ detail: string }> {
  return apiFetch(`/api/routes/${id}`, { method: 'DELETE', token })
}
