import { apiFetch } from './client'
import { normalizeCoordinates } from '../utils/geo'
import type { Alert, AlertSeverity, AlertSource } from '../types'

// `normalizeCoordinates` runs once here, at the API boundary — see `routes.ts`'s identical
// comment and `utils/geo.ts`'s own contract.
function normalizeAlert(a: Alert): Alert {
  return { ...a, coordinates: normalizeCoordinates(a.coordinates) }
}

export interface AlertFilters {
  severity?: AlertSeverity
  source?: AlertSource
  reviewed?: boolean
  route_id?: string
}

/** `filters` maps 1:1 onto `GET /api/alerts`'s existing query params
 * (`app/routers/alerts.py`'s `severity`/`source`/`reviewed`/`route_id`) — all already supported
 * server-side, just not previously exposed from this client. Omitted for backward compatibility
 * with existing callers (`LiveOps.tsx`, `RouteHistory.tsx`) that still want the full list. */
export function listAlerts(token: string, filters: AlertFilters = {}): Promise<Alert[]> {
  const params = new URLSearchParams()
  if (filters.severity) params.set('severity', filters.severity)
  if (filters.source) params.set('source', filters.source)
  if (filters.reviewed !== undefined) params.set('reviewed', String(filters.reviewed))
  if (filters.route_id) params.set('route_id', filters.route_id)
  const qs = params.toString()
  return apiFetch<Alert[]>(`/api/alerts${qs ? `?${qs}` : ''}`, { token }).then((as) =>
    as.map(normalizeAlert),
  )
}

export function getAlert(id: string, token: string): Promise<Alert> {
  return apiFetch<Alert>(`/api/alerts/${id}`, { token }).then(normalizeAlert)
}

/** Guardian/root_admin only. `severity_level` must be strictly lower than the alert's current
 * one (the backend 422s otherwise); `reason` is required. Also marks the alert reviewed. */
export function deescalateAlert(
  id: string,
  input: { severity_level: AlertSeverity; reason: string },
  token: string,
): Promise<Alert> {
  return apiFetch<Alert>(`/api/alerts/${id}/de-escalate`, {
    method: 'POST',
    body: input,
    token,
  }).then(normalizeAlert)
}

export function reviewAlert(
  id: string,
  input: { reviewed_by_operator: boolean; operator_notes: string },
  token: string,
): Promise<Alert> {
  return apiFetch<Alert>(`/api/alerts/${id}`, { method: 'PUT', body: input, token }).then(
    normalizeAlert,
  )
}
