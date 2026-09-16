import { apiFetch } from './client'
import { sha256Hex } from '../utils/crypto'
import type { Role, User } from '../types'

export interface UserCreateInput {
  email: string
  password: string
  role: Role
  first_name: string
  last_name: string
  phone_number: string
  is_active?: boolean
}

export interface UserUpdateInput {
  email?: string
  /** Raw password — hashed here before it ever leaves the page, same as `login()`. Omit to
   * leave the current password unchanged. */
  password?: string
  role?: Role
  first_name?: string
  last_name?: string
  phone_number?: string
  is_active?: boolean
}

/** `GET /api/users` — for an `admin` caller the backend already scopes this to guardian-role
 * accounts only (see `src/backend-argus/app/routers/users.py`); no client-side filtering
 * needed on top of that. */
export function listUsers(token: string): Promise<User[]> {
  return apiFetch<User[]>('/api/users', { token })
}

export async function createUser(input: UserCreateInput, token: string): Promise<User> {
  const { password, ...rest } = input
  return apiFetch<User>('/api/users', {
    method: 'POST',
    body: { ...rest, password: await sha256Hex(password) },
    token,
  })
}

export async function updateUser(
  id: string,
  input: UserUpdateInput,
  token: string,
): Promise<User> {
  const { password, ...rest } = input
  const body = password !== undefined ? { ...rest, password: await sha256Hex(password) } : rest
  return apiFetch<User>(`/api/users/${id}`, { method: 'PUT', body, token })
}

/** A hard delete, not a deactivation — use `updateUser(id, { is_active: false }, token)` to
 * deactivate an account instead. */
export function deleteUser(id: string, token: string): Promise<{ detail: string }> {
  return apiFetch(`/api/users/${id}`, { method: 'DELETE', token })
}
