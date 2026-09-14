/**
 * Base request wrapper for `src/backend-argus` (INTEGRATION.md gap #1). Every screen that talks
 * to the backend goes through `apiFetch` rather than calling `fetch` directly, so the base URL,
 * auth header, and FastAPI's error-body shape are handled in exactly one place.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/**
 * A session-expiry hook, not a per-request concern: `src/context/AuthContext.tsx` registers its
 * `logout` here on mount, so any `apiFetch` call that comes back `401` (an expired/invalid JWT —
 * `JWT_EXPIRE_MINUTES` has no refresh flow, see the backend's own CLAUDE.md) clears the session
 * automatically. That flips `ProtectedRoute`'s check on the next render and bounces the user back
 * to `/login` the same way a missing session does at initial load — one mechanism for both
 * cases. No screen makes authenticated calls yet besides login itself, but wiring this now means
 * the next one that does gets it for free instead of reimplementing 401-handling per screen.
 */
let onUnauthorized: (() => void) | null = null

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler
}

interface ApiFetchOptions {
  method?: string
  body?: unknown
  token?: string | null
}

export async function apiFetch<T>(path: string, opts: ApiFetchOptions = {}): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (opts.token) headers.Authorization = `Bearer ${opts.token}`

  const res = await fetch(`${BASE_URL}${path}`, {
    method: opts.method ?? 'GET',
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  })

  if (!res.ok) {
    if (res.status === 401) onUnauthorized?.()
    const body = await res.json().catch(() => null)
    // FastAPI's `detail` is a string for a plain HTTPException, but a list of validation-error
    // objects for a raw 422 — normalize both into one readable message.
    const detail =
      typeof body?.detail === 'string'
        ? body.detail
        : Array.isArray(body?.detail)
          ? body.detail.map((e: { msg?: string }) => e.msg).join('; ')
          : 'Request failed'
    throw new ApiError(res.status, detail)
  }

  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}
