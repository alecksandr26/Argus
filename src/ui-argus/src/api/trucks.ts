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

/** Where the env file's SMS phone came from (route guardian contracts G5). */
export type AlertPhoneSource = 'route_guardian' | 'truck'

/**
 * `POST /api/trucks/:id/rotate-key` — the plaintext key, returned exactly once, plus the SMS
 * phone the backend resolved for `argus-device.env` (G5). The phone fields are optional because a
 * backend older than the route-guardian change doesn't send them; `issuedPhone` in
 * `utils/deviceEnv.ts` then falls back to the truck's own phone.
 */
export interface DeviceKey {
  id_truck: string
  device_api_key: string
  alert_phone?: string | null
  alert_phone_source?: AlertPhoneSource | null
  /** The route whose guardian was picked, or the route considered for a truck fallback. */
  id_route?: string | null
  guardian_name?: string | null
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

/**
 * Issues a new device key for this truck, invalidating any previous one. With `idRoute`, the
 * backend takes that route's guardian phone first (G5); without it, it picks the truck's current
 * or next route itself.
 */
export function rotateDeviceKey(id: string, token: string, idRoute?: string): Promise<DeviceKey> {
  return apiFetch<DeviceKey>(`/api/trucks/${id}/rotate-key`, {
    method: 'POST',
    body: idRoute ? { id_route: idRoute } : undefined,
    token,
  })
}

export function revokeDeviceKey(id: string, token: string): Promise<Truck> {
  return apiFetch<Truck>(`/api/trucks/${id}/device-key`, { method: 'DELETE', token })
}
