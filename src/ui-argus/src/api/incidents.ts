import { apiFetch } from './client'
import { normalizeAlert } from './alerts'
import type { Incident } from '../types'

/** Every incident read goes through this, so its embedded alerts get the same coordinate
 * normalization as `GET /api/alerts`. */
export function normalizeIncident(i: Incident): Incident {
  return { ...i, alerts: i.alerts.map(normalizeAlert) }
}

export interface IncidentFilters {
  route_id?: string
  /** Server default `all`. */
  status?: 'open' | 'closed' | 'all'
  /** 1..500, server default 100. */
  limit?: number
}

/** `GET /api/incidents` — ordered by `last_alert_at` descending (C3.6). Alert history reads the
 * closed ones from here. */
export function listIncidents(token: string, filters: IncidentFilters = {}): Promise<Incident[]> {
  const params = new URLSearchParams()
  if (filters.route_id) params.set('route_id', filters.route_id)
  if (filters.status) params.set('status', filters.status)
  if (filters.limit !== undefined) params.set('limit', String(filters.limit))
  const qs = params.toString()
  return apiFetch<Incident[]>(`/api/incidents${qs ? `?${qs}` : ''}`, { token }).then((is) =>
    is.map(normalizeIncident),
  )
}

export function getIncident(id: string, token: string): Promise<Incident> {
  return apiFetch<Incident>(`/api/incidents/${id}`, { token }).then(normalizeIncident)
}

/** `POST /api/incidents/{id}/close` — guardian + root_admin only. Closes every alert of the
 * incident. A critical incident needs a non-empty note (the backend 422s otherwise; check it
 * client-side first with `closeNoteError` from `utils/incidents.ts`); 409 if already closed. */
export function closeIncident(id: string, note: string, token: string): Promise<Incident> {
  return apiFetch<Incident>(`/api/incidents/${id}/close`, {
    method: 'POST',
    body: { note },
    token,
  }).then(normalizeIncident)
}
