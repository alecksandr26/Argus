import { apiFetch } from './client'
import type { Truck } from '../types'

export type TruckInput = Omit<
  Truck,
  | 'id_truck'
  | 'created_at'
  | 'updated_at'
  | 'has_device_key'
  | 'device_key_issued_at'
  | 'device_last_seen_at'
>

/** `POST /api/trucks/:id/rotate-key` — the plaintext key, returned exactly once. */
export interface DeviceKey {
  id_truck: string
  device_api_key: string
}

export function listTrucks(token: string): Promise<Truck[]> {
  return apiFetch<Truck[]>('/api/trucks', { token })
}

export function createTruck(input: TruckInput, token: string): Promise<Truck> {
  return apiFetch<Truck>('/api/trucks', { method: 'POST', body: input, token })
}

export function updateTruck(
  id: string,
  input: Partial<TruckInput>,
  token: string,
): Promise<Truck> {
  return apiFetch<Truck>(`/api/trucks/${id}`, { method: 'PUT', body: input, token })
}

export function getTruck(id: string, token: string): Promise<Truck> {
  return apiFetch<Truck>(`/api/trucks/${id}`, { token })
}

/** Issues a new device key for this truck, invalidating any previous one. */
export function rotateDeviceKey(id: string, token: string): Promise<DeviceKey> {
  return apiFetch<DeviceKey>(`/api/trucks/${id}/rotate-key`, { method: 'POST', token })
}

export function revokeDeviceKey(id: string, token: string): Promise<Truck> {
  return apiFetch<Truck>(`/api/trucks/${id}/device-key`, { method: 'DELETE', token })
}
