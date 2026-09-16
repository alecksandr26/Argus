import { apiFetch } from './client'
import { sha256Hex } from '../utils/crypto'
import type { User } from '../types'

export interface MeUpdateInput {
  email?: string
  /** Raw password — hashed here before it ever leaves the page. Omit to leave unchanged. */
  password?: string
  first_name?: string
  last_name?: string
  phone_number?: string
}

/** `GET /api/auth/me` — self-service profile read, available to every authenticated role
 * regardless of `/api/users` access. */
export function getMe(token: string): Promise<User> {
  return apiFetch<User>('/api/auth/me', { token })
}

/** `PUT /api/auth/me` — self-service profile edit. There is deliberately no way to send a
 * `role`/`is_active` change through this call; that stays exclusively `root_admin`'s (or, for
 * guardians, a scoped `admin`'s) job via `/api/users/:id`. */
export async function updateMe(input: MeUpdateInput, token: string): Promise<User> {
  const { password, ...rest } = input
  const body = password !== undefined ? { ...rest, password: await sha256Hex(password) } : rest
  return apiFetch<User>('/api/auth/me', { method: 'PUT', body, token })
}
