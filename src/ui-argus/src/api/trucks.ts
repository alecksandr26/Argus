import { apiFetch } from './client'
import type { Truck } from '../types'

export type TruckInput = Omit<Truck, 'id_truck' | 'created_at' | 'updated_at'>

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

export function deleteTruck(id: string, token: string): Promise<{ detail: string }> {
  return apiFetch(`/api/trucks/${id}`, { method: 'DELETE', token })
}
