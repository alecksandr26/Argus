import { createHash, randomUUID } from 'node:crypto'
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

/**
 * The device side of the final-tweaks contracts (C2, `docs/designs/final-tweaks-contracts.md`):
 * `POST /api/devices/me/report` is the only device write, authenticated by a per-truck device
 * API key (never a JWT). Specs act as a truck's ESP32 with these helpers: the same endpoint and
 * body `src/simulator-argus` and the firmware post.
 */
export type Vigilance = 'low' | 'medium' | 'critical' | 'unknown'

export interface ReportPoint {
  age_s: number
  lat: number
  lon: number
  speed: number
  vigilance: Vigilance
  last_known: boolean
  location_age_s: number
}

export interface ReportPosition {
  lat: number
  lon: number
  last_known: boolean
  location_age_s: number
}

export interface ReportEvent {
  client_event_id: string
  episode: string
  kind: 'alert' | 'ok'
  severity: 'medium' | 'critical' | null
  alert_type: string | null
  age_s: number
  delivery: 'live' | 'dlq'
  queued_seconds: number
  position: ReportPosition | null
  speed: number | null
  ai_metadata: {
    model: string | null
    scores: { not_drowsy: number; drowsy: number } | null
  } | null
}

export interface ReportBody {
  points: ReportPoint[]
  event?: ReportEvent | null
}

export interface ReportResult {
  points: {
    stored: number
    route: string | null
    status: 'ok' | 'no_active_route' | 'route_finished'
  }
  event: {
    status: 'created' | 'duplicate' | 'rejected'
    reason: 'route_finished' | 'no_route' | 'too_old' | 'wrong_truck' | 'invalid' | null
    id_alert: string | null
  } | null
  active_route: string | null
}

/** 32 lowercase hex chars, like a Pi record id (`client_event_id`). */
export function newEventId(): string {
  return randomUUID().replace(/-/g, '')
}

/** 12 lowercase hex chars, like a Pi `payload.episode`. */
export function newEpisode(): string {
  return randomUUID().replace(/-/g, '').slice(0, 12)
}

/** One GPS point: a live fix `age_s` seconds before the request, by default. */
export function reportPoint(overrides: Partial<ReportPoint> = {}): ReportPoint {
  return {
    age_s: 1,
    lat: 19.05,
    lon: -98.25,
    speed: 82,
    vigilance: 'low',
    last_known: false,
    location_age_s: 0,
    ...overrides,
  }
}

/** A live alert event, critical `eyes_closed` by default (each call: a new id and episode). */
export function alertEvent(overrides: Partial<ReportEvent> = {}): ReportEvent {
  return {
    client_event_id: newEventId(),
    episode: newEpisode(),
    kind: 'alert',
    severity: 'critical',
    alert_type: 'eyes_closed',
    age_s: 0.5,
    delivery: 'live',
    queued_seconds: 0,
    position: { lat: 19.05, lon: -98.25, last_known: false, location_age_s: 0 },
    speed: 82,
    ai_metadata: { model: 'parts-rules-v1', scores: { not_drowsy: 0.12, drowsy: 0.88 } },
    ...overrides,
  }
}

/** The driver-OK event of `episode` (it sets `driver_ok_at`; it never closes anything). */
export function okEvent(episode: string, overrides: Partial<ReportEvent> = {}): ReportEvent {
  return alertEvent({
    episode,
    kind: 'ok',
    severity: null,
    alert_type: null,
    ai_metadata: null,
    ...overrides,
  })
}

/** Mints (or rotates) a truck's device API key with a root_admin/admin JWT; returns the
 * plaintext key, which the backend shows only once. */
export async function rotateDeviceKey(
  request: APIRequestContext,
  token: string,
  truckId: string,
): Promise<string> {
  const res = await request.post(`${BACKEND_URL}/api/trucks/${truckId}/rotate-key`, {
    headers: authHeaders(token),
  })
  if (!res.ok()) {
    throw new Error(`rotateDeviceKey(${truckId}) failed: ${res.status()} ${await res.text()}`)
  }
  return (await res.json()).device_api_key as string
}

/** Posts one combined report as the truck that owns `deviceKey`. Returns the HTTP status and
 * the parsed body without asserting, so a spec can check a 200 with per-part results
 * (`rejected route_finished`, ...) and an error status alike. */
export async function postReport(
  request: APIRequestContext,
  deviceKey: string,
  body: ReportBody,
): Promise<{ status: number; body: ReportResult }> {
  const res = await request.post(`${BACKEND_URL}/api/devices/me/report`, {
    headers: { 'X-Device-Api-Key': deviceKey },
    data: body,
  })
  const text = await res.text()
  let parsed: unknown = null
  try {
    parsed = text ? JSON.parse(text) : null
  } catch {
    parsed = text
  }
  return { status: res.status(), body: parsed as ReportResult }
}
