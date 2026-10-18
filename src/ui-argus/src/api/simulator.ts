import { apiFetch } from './client'

/** `src/backend-argus/app/schemas/simulator.py`'s `SimulatorStatusOut`. root_admin only. */
export interface SimulatorStatus {
  state: 'stopped' | 'starting' | 'running'
  session_minutes: number
  started_at: string | null
  auto_off_at: string | null
  truck_count: number | null
  quota_minutes: number
  quota_used_minutes: number
  quota_remaining_minutes: number
  sessions_remaining: number
}

export function getSimulatorStatus(token: string): Promise<SimulatorStatus> {
  return apiFetch<SimulatorStatus>('/api/simulator', { token })
}

/** No duration: every session is the server's fixed length (10 minutes). */
export function startSimulator(truckCount: number, token: string): Promise<SimulatorStatus> {
  return apiFetch<SimulatorStatus>('/api/simulator/start', {
    method: 'POST',
    body: { truck_count: truckCount },
    token,
  })
}

export function stopSimulator(token: string): Promise<SimulatorStatus> {
  return apiFetch<SimulatorStatus>('/api/simulator/stop', { method: 'POST', token })
}
