import { apiFetch } from './client'

/** `GET /health` (contracts G6). `version` is the backend's git SHA, or 'dev' for a local build;
 * a backend older than G6 doesn't send it. */
export interface Health {
  status: string
  version?: string
}

export function getHealth(): Promise<Health> {
  return apiFetch<Health>('/health')
}
