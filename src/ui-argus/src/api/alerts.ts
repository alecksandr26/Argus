import { apiFetch } from './client'
import { normalizeCoordinates } from '../utils/geo'
import type { Alert, AlertSeverity, AlertSource } from '../types'

/** `normalizeCoordinates` runs once here, at the API boundary — see `routes.ts`'s identical
 * comment and `utils/geo.ts`'s own contract. `coordinates` is `null` when the position is
 * unknown (C3.2), which passes through untouched. Exported because incidents embed full alerts
 * (`api/incidents.ts`, `api/live.ts`). */
export function normalizeAlert(a: Alert): Alert {
  return {
    ...a,
    coordinates: a.coordinates == null ? null : normalizeCoordinates(a.coordinates),
  }
}

export interface AlertFilters {
  severity?: AlertSeverity
  source?: AlertSource
  reviewed?: boolean
  route_id?: string
  /** Only the alerts of one incident (C3.6). */
  incident_id?: string
}

/** `filters` maps 1:1 onto `GET /api/alerts`'s query params
 * (`severity`/`source`/`reviewed`/`route_id`/`incident_id`). */
export function listAlerts(token: string, filters: AlertFilters = {}): Promise<Alert[]> {
  const params = new URLSearchParams()
  if (filters.severity) params.set('severity', filters.severity)
  if (filters.source) params.set('source', filters.source)
  if (filters.reviewed !== undefined) params.set('reviewed', String(filters.reviewed))
  if (filters.route_id) params.set('route_id', filters.route_id)
  if (filters.incident_id) params.set('incident_id', filters.incident_id)
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

/** Guardian/root_admin review notes on one alert. Closing is per incident (`closeIncident` in
 * `api/incidents.ts`), not here. */
export function reviewAlert(
  id: string,
  input: { reviewed_by_operator: boolean; operator_notes: string },
  token: string,
): Promise<Alert> {
  return apiFetch<Alert>(`/api/alerts/${id}`, { method: 'PUT', body: input, token }).then(
    normalizeAlert,
  )
}
