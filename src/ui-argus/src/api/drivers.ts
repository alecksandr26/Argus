import { apiFetch } from './client'
import type { Driver } from '../types'

export type DriverInput = Omit<Driver, 'id_driver' | 'created_at' | 'updated_at'>

export function listDrivers(token: string): Promise<Driver[]> {
  return apiFetch<Driver[]>('/api/drivers', { token })
}

export function createDriver(input: DriverInput, token: string): Promise<Driver> {
  return apiFetch<Driver>('/api/drivers', { method: 'POST', body: input, token })
}

export function updateDriver(
  id: string,
  input: Partial<DriverInput>,
  token: string,
): Promise<Driver> {
  return apiFetch<Driver>(`/api/drivers/${id}`, { method: 'PUT', body: input, token })
}

export function deleteDriver(id: string, token: string): Promise<{ detail: string }> {
  return apiFetch(`/api/drivers/${id}`, { method: 'DELETE', token })
}
