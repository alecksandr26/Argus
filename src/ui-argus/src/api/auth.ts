import { apiFetch } from './client'
import { sha256Hex } from '../utils/crypto'
import type { LoginResponse } from '../types'

/**
 * `POST /api/auth/login`. The password never leaves the page in the clear — see
 * `src/utils/crypto.ts`'s `sha256Hex` doc comment for why the digest is sent instead.
 */
export async function login(email: string, password: string): Promise<LoginResponse> {
  const passwordDigest = await sha256Hex(password)
  return apiFetch<LoginResponse>('/api/auth/login', {
    method: 'POST',
    body: { email, password: passwordDigest },
  })
}
