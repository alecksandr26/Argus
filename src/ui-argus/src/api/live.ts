import { apiFetch } from './client'
import { normalizeIncident } from './incidents'
import { normalizeCoordinates } from '../utils/geo'
import type { LiveResponse } from '../types'

export interface LiveQuery {
  /** The previous response's `server_time`: only track points received after it come back. */
  since?: string | null
  /** One route only (any status) — the route view. */
  route?: string | null
}

/** `GET /api/live` (C3.6) — the one poll behind Live operations and the route view: routes with
 * their newest position, all open incidents in full, and the track points since `since`. Merge
 * successive responses with `mergeLive()` (`utils/live.ts`). */
export function getLive(token: string, query: LiveQuery = {}): Promise<LiveResponse> {
  const params = new URLSearchParams()
  if (query.since) params.set('since', query.since)
  if (query.route) params.set('route', query.route)
  const qs = params.toString()
  return apiFetch<LiveResponse>(`/api/live${qs ? `?${qs}` : ''}`, { token }).then((r) => ({
    ...r,
    routes: r.routes.map((route) => ({
      ...route,
      destination_coordinates: normalizeCoordinates(route.destination_coordinates),
    })),
    incidents: r.incidents.map(normalizeIncident),
  }))
}
