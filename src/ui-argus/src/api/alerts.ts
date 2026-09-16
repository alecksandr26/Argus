import { apiFetch } from './client'
import { normalizeCoordinates } from '../utils/geo'
import type { Alert } from '../types'

// `normalizeCoordinates` runs once here, at the API boundary — see `routes.ts`'s identical
// comment and `utils/geo.ts`'s own contract.
function normalizeAlert(a: Alert): Alert {
  return { ...a, coordinates: normalizeCoordinates(a.coordinates) }
}

export function listAlerts(token: string): Promise<Alert[]> {
  return apiFetch<Alert[]>('/api/alerts', { token }).then((as) => as.map(normalizeAlert))
}

export function getAlert(id: string, token: string): Promise<Alert> {
  return apiFetch<Alert>(`/api/alerts/${id}`, { token }).then(normalizeAlert)
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
