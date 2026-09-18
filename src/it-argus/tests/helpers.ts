import { createHash } from 'node:crypto'
import type { APIRequestContext, Page } from '@playwright/test'

/**
 * Shared across every spec file — pulled out once `users.spec.ts`/`trucks.spec.ts`/
 * `drivers.spec.ts` needed the same login helper and bootstrap credentials `auth.spec.ts`
 * already had inline.
 */
export const ROOT_ADMIN_EMAIL = 'admin@argus.dev'
export const ROOT_ADMIN_PASSWORD = 'changeme123'

/**
 * `it-argus`'s Mongo volume is NOT wiped by a plain `docker compose up` rerun (only
 * `down -v` clears it — see this module's CLAUDE.md), so a spec hardcoding a fixed email or
 * plate number will 409 on the second run. Every record a new spec creates should carry this
 * suffix instead.
 */
export function uniqueSuffix(): string {
  return `${Date.now()}-${Math.floor(Math.random() * 1e6)}`
}

export async function login(page: Page, email: string, password: string) {
  await page.getByLabel('Email address').fill(email)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  // Wait for the attempt to actually resolve — navigation away from /login on success, or the
  // inline error alert on a rejected login — before returning. Without this, a caller's very
  // next action (e.g. a further `page.goto()`) can race the in-flight `POST /api/auth/login`
  // fetch and abort it before the app ever stores the session: a real bug hit writing
  // `users.spec.ts`/`trucks.spec.ts`/`drivers.spec.ts`, which — unlike `auth.spec.ts`'s own
  // tests — don't always follow `login()` with an `expect(...).toHaveURL(...)` (whose own
  // polling/retry incidentally gave the fetch enough time to land). Not hardcoding an expected
  // destination here also sidesteps a second real surprise: React Router's `state.from`
  // preserves wherever a session was signed out *from*, so logging back in after signing out on
  // `/access` lands back on `/access`, not `/` — the caller, not this helper, should assert the
  // specific destination when that matters.
  await Promise.race([
    page.waitForURL((url) => !url.pathname.endsWith('/login')),
    page.getByRole('alert').waitFor({ state: 'visible' }),
  ])
}

export async function logout(page: Page) {
  await page.getByText('Sign out').click()
}

/**
 * `it-argus`'s own `docker-compose.yml` shares `ui-argus`'s network namespace (see that file's
 * `network_mode` comment), so the Docker-internal hostname `backend-argus` is reachable from
 * this test process directly — the same DNS visibility the browser under test already needs for
 * its own `fetch` calls. Override for a differently-shaped local run (e.g. the repo-root
 * `docker compose up` stack, which publishes the backend at `http://localhost:8000`).
 */
export const BACKEND_URL = process.env.IT_BACKEND_URL ?? 'http://backend-argus:8000'

/**
 * Every `password` field on the backend's API boundary (`LoginRequest`, `UserCreate`, ...)
 * takes a SHA-256 hex digest of the real password, not the raw password — `ui-argus` computes
 * this client-side via `crypto.subtle`; a Playwright spec talking to the backend directly (no
 * browser involved) has to reproduce that same digest itself, via Node's own `crypto` module.
 */
export function sha256Hex(raw: string): string {
  return createHash('sha256').update(raw).digest('hex')
}

/** Logs in against the real backend (no browser) and returns the JWT — for specs that need to
 * seed data via REST before a page ever loads (route/status/alert ingestion, user creation for
 * a role a UI flow isn't the point of exercising). */
export async function restLogin(
  request: APIRequestContext,
  email: string,
  password: string,
): Promise<string> {
  const res = await request.post(`${BACKEND_URL}/api/auth/login`, {
    data: { email, password: sha256Hex(password) },
  })
  if (!res.ok()) {
    throw new Error(`restLogin(${email}) failed: ${res.status()} ${await res.text()}`)
  }
  const body = await res.json()
  return body.access_token as string
}

export function authHeaders(token: string): { Authorization: string } {
  return { Authorization: `Bearer ${token}` }
}
