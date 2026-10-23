import { createHash, randomUUID } from 'node:crypto'
import type { APIRequestContext, APIResponse, Locator, Page } from '@playwright/test'

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

/** A live alert event, critical `eyes_closed` by default (each call: a new id and episode).
 * `age_s` defaults to 0: the backend matches an event to the route whose
 * `actual_departure..actual_arrival` contains `received_at - age_s` (C2.2 rule 4), so on a route
 * started a moment ago any positive age could land before its departure (`rejected no_route`).
 * Specs that need an older event create the route with `departedSecondsAgo`. */
export function alertEvent(overrides: Partial<ReportEvent> = {}): ReportEvent {
  return {
    client_event_id: newEventId(),
    episode: newEpisode(),
    kind: 'alert',
    severity: 'critical',
    alert_type: 'eyes_closed',
    age_s: 0,
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

/** Posts a report and fails unless the backend answered 200 (the per-part results are still
 * the caller's to check). */
export async function postReportOk(
  request: APIRequestContext,
  deviceKey: string,
  body: ReportBody,
): Promise<ReportResult> {
  const res = await postReport(request, deviceKey, body)
  if (res.status !== 200) {
    throw new Error(`postReport failed: ${res.status} ${JSON.stringify(res.body)}`)
  }
  return res.body
}

// --- Specs that need ui-argus's final-tweaks screens ---------------------------------------

/**
 * The incident feed, the close and stop dialogs and the position labels land in `ui-argus`
 * with team T5's branch, which merges *after* the backend + simulator + it-argus PR. Specs (or
 * the UI half of a spec) that assert on those screens are skipped unless the run sets
 * `IT_FINAL_TWEAKS_UI=1`, which the integration run does once T5's UI is in the stack. The
 * API-level checks of the same behaviour always run.
 */
export const FINAL_TWEAKS_UI = process.env.IT_FINAL_TWEAKS_UI === '1'
export const NEEDS_FINAL_TWEAKS_UI =
  "needs ui-argus's final-tweaks UI (T5: incident feed, close/stop dialogs, position labels); " +
  'set IT_FINAL_TWEAKS_UI=1 once it is in the stack'

// --- UI test hooks ui-argus pins for these specs (contracts C3.7 [sync 3]) -----------------

/** A tier section of the incident feed, by its region name, not its header text (the header
 * adds a count, e.g. `Older than 1 h · 1`). */
export type IncidentTier = 'Critical' | 'Medium' | 'Older than 1 h'

export function tierRegion(page: Page, tier: IncidentTier): Locator {
  return page.getByRole('region', { name: `${tier} incidents` })
}

/**
 * The incident card holding an alert with this alert-type label (e.g. `Eyes closed`), scoped
 * so a test never picks up another incident's button or the route view's `Open`/`Closed` filter
 * chips. The label match is exact, so `Eyes closed` doesn't also match `Head down, eyes closed`.
 * The card is ui-argus's `<article>` (`IncidentFeed.tsx`'s `IncidentCard`).
 */
export function incidentCard(scope: Page | Locator, alertTypeLabel: string): Locator {
  // `has` is queried relative to each card, so the inner locator must start from the page, not
  // from `scope` (a region-scoped inner locator would look for the region inside the card).
  const page = 'page' in scope ? scope.page() : scope
  return scope
    .getByRole('article')
    .filter({ has: page.getByText(alertTypeLabel, { exact: true }) })
}

/**
 * A text in the incident card's *own* rows (its header and its pill row: `Driver OK since`, the
 * replayed badge, `Closed HH:MM`), never inside one of its alert cards. Several of those pills
 * exist at both levels (an alert of the episode the driver confirmed OK also shows `Driver OK
 * since`, a replayed alert its own replayed badge), so an unscoped `card.getByText(...)` would
 * match twice and fail strict mode whenever the alert card shows it too. The card's direct
 * `div` children that hold no alert card are exactly its own rows.
 */
export function incidentOwnText(card: Locator, text: string | RegExp): Locator {
  return card
    .locator(':scope > div')
    .filter({ hasNot: card.page().getByTestId('alert-card') })
    .getByText(text)
}

/** `Closed HH:MM`, the close time as the UI shows it (confirmation and closed pill). */
export const CLOSED_AT = /Closed \d{1,2}:\d{2}/

/** The `role="status"` confirmation the UI shows after a successful close. */
export function closeConfirmation(page: Page): Locator {
  return page.getByRole('status').filter({ hasText: CLOSED_AT })
}

/** The close dialog (`role="dialog"` named `Close incident`). */
export function closeDialog(page: Page): Locator {
  return page.getByRole('dialog', { name: 'Close incident' })
}

// --- Fleet setup over REST (root_admin/admin JWT) ------------------------------------------

async function expectCreated<T>(what: string, res: APIResponse): Promise<T> {
  if (res.status() !== 201) {
    throw new Error(`${what} failed: ${res.status()} ${await res.text()}`)
  }
  return (await res.json()) as T
}

export async function createTruck(request: APIRequestContext, token: string, plate: string) {
  const res = await request.post(`${BACKEND_URL}/api/trucks`, {
    headers: authHeaders(token),
    data: {
      plate_number: plate,
      brand: 'Kenworth',
      model: 'T680',
      company_number: `UNIT-${plate}`,
      operative_status: 'active',
    },
  })
  return expectCreated<{ id_truck: string; plate_number: string }>(`createTruck(${plate})`, res)
}

export async function createDriver(request: APIRequestContext, token: string, license: string) {
  const res = await request.post(`${BACKEND_URL}/api/drivers`, {
    headers: authHeaders(token),
    data: {
      first_name: 'Dana',
      last_name: 'Driver',
      license_number: license,
      license_expiration: '2030-01-01',
      phone_number: '5552223333',
      emergency_contact_name: 'Emergency Contact',
      emergency_contact_phone: '5550001111',
      blood_type: 'O+',
      operative_status: 'on_route',
    },
  })
  return expectCreated<{ id_driver: string; first_name: string; last_name: string }>(
    `createDriver(${license})`,
    res,
  )
}

/**
 * Creates a route already `in_progress`. `departedSecondsAgo` (default 3600) is sent as
 * `actual_departure` (C3.1 [sync 1]), so events reported with an `age_s` up to that long are
 * matched to this route by time (C2.2 rule 4) instead of landing before its departure.
 */
export async function createRouteRest(
  request: APIRequestContext,
  token: string,
  opts: {
    driverId: string
    truckId: string
    origin: string
    destination: string
    departedSecondsAgo?: number
  },
) {
  const departedAt = new Date(Date.now() - (opts.departedSecondsAgo ?? 3600) * 1000)
  const res = await request.post(`${BACKEND_URL}/api/routes`, {
    headers: authHeaders(token),
    data: {
      id_driver: opts.driverId,
      id_truck: opts.truckId,
      origin_name: opts.origin,
      destination_name: opts.destination,
      destination_coordinates: { lat: 19.04, lon: -98.2 },
      estimated_departure: departedAt.toISOString(),
      actual_departure: departedAt.toISOString(),
      operative_status: 'in_progress',
    },
  })
  return expectCreated<Route>(`createRouteRest(${opts.origin})`, res)
}

export async function createUserRest(
  request: APIRequestContext,
  token: string,
  opts: { email: string; password: string; role: 'guardian' | 'admin' },
) {
  const res = await request.post(`${BACKEND_URL}/api/users`, {
    headers: authHeaders(token),
    data: {
      email: opts.email,
      password: sha256Hex(opts.password),
      role: opts.role,
      first_name: 'Test',
      last_name: opts.role === 'guardian' ? 'Guardian' : 'Admin',
      phone_number: '5551110000',
    },
  })
  await expectCreated<unknown>(`createUserRest(${opts.email})`, res)
}

export interface ActiveTruck {
  plate: string
  truckId: string
  driverId: string
  routeId: string
  origin: string
  destination: string
  deviceKey: string
}

/** A fresh truck + driver + in-progress route + device key, all named with `suffix`. */
export async function setupActiveTruck(
  request: APIRequestContext,
  token: string,
  suffix: string,
  opts: { departedSecondsAgo?: number; platePrefix?: string } = {},
): Promise<ActiveTruck> {
  const plate = `${opts.platePrefix ?? 'IT'}-${suffix}`
  const origin = `Origin-${suffix}`
  const destination = `Dest-${suffix}`
  const truck = await createTruck(request, token, plate)
  const driver = await createDriver(request, token, `LIC-${suffix}`)
  const route = await createRouteRest(request, token, {
    driverId: driver.id_driver,
    truckId: truck.id_truck,
    origin,
    destination,
    departedSecondsAgo: opts.departedSecondsAgo,
  })
  const deviceKey = await rotateDeviceKey(request, token, truck.id_truck)
  return {
    plate,
    truckId: truck.id_truck,
    driverId: driver.id_driver,
    routeId: route.id_route,
    origin,
    destination,
    deviceKey,
  }
}

/** A user with `role`, logged in over REST; returns its credentials and JWT. */
export async function createUserAndLogin(
  request: APIRequestContext,
  rootToken: string,
  role: 'guardian' | 'admin',
  suffix: string,
) {
  const email = `${role}-${suffix}@argus.dev`
  const password = `${role}-pass-123`
  await createUserRest(request, rootToken, { email, password, role })
  return { email, password, token: await restLogin(request, email, password) }
}

// --- Backend -> UI shapes (contracts C3), only the fields the specs read --------------------

export interface Coordinates {
  lat: number
  lon: number
}

export interface Alert {
  id_alert: string
  id_route: string
  alert_type: string
  source: string
  severity_level: 'low' | 'medium' | 'critical'
  episode: string | null
  id_incident: string | null
  delivery: 'live' | 'dlq'
  queued_seconds: number
  driver_ok_at: string | null
  closed_at: string | null
  coordinates: Coordinates | null
  position_unknown: boolean
  location_is_last_known: boolean
  location_age_s: number | null
  speed_at_event: number | null
  client_event_id: string | null
  related_alert_id: string | null
  reviewed_by_operator: boolean
  timestamp: string
}

export interface Incident {
  id_incident: string
  id_route: string
  severity: 'medium' | 'critical'
  tier: 'critical' | 'medium' | 'old'
  first_alert_at: string
  last_alert_at: string
  alert_count: number
  driver_ok_at: string | null
  has_replayed: boolean
  closed_at: string | null
  closed_by: string | null
  close_note: string
  route_ended: boolean
  truck_plate_number: string | null
  alerts: Alert[]
}

export interface Route {
  id_route: string
  operative_status: string
  incident_state: 'fine' | 'medium' | 'critical'
  stop_reason: string | null
  stopped_by: string | null
  stopped_at: string | null
  actual_departure: string | null
  actual_arrival: string | null
}

export interface LivePosition {
  t: string
  lat: number
  lon: number
  speed: number
  vigilance: Vigilance
  last_known: boolean
  location_age_s: number | null
}

export interface LiveResponse {
  server_time: string
  routes: (Route & { truck_plate_number: string | null; latest_position: LivePosition | null })[]
  incidents: Incident[]
  track: (LivePosition & { id_route: string })[]
}

async function getJson<T>(request: APIRequestContext, token: string, path: string): Promise<T> {
  const res = await request.get(`${BACKEND_URL}${path}`, { headers: authHeaders(token) })
  if (!res.ok()) {
    throw new Error(`GET ${path} failed: ${res.status()} ${await res.text()}`)
  }
  return (await res.json()) as T
}

export function getRoute(request: APIRequestContext, token: string, routeId: string) {
  return getJson<Route>(request, token, `/api/routes/${routeId}`)
}

export function getAlert(request: APIRequestContext, token: string, alertId: string) {
  return getJson<Alert>(request, token, `/api/alerts/${alertId}`)
}

/** `GET /api/incidents?route_id=` (newest `last_alert_at` first). */
export function listIncidents(
  request: APIRequestContext,
  token: string,
  routeId: string,
  status: 'open' | 'closed' | 'all' = 'all',
) {
  return getJson<Incident[]>(
    request,
    token,
    `/api/incidents?route_id=${encodeURIComponent(routeId)}&status=${status}`,
  )
}

/** `GET /api/live?route=` (the one route, its open incidents in feed order, its track). */
export function getLive(request: APIRequestContext, token: string, routeId: string) {
  return getJson<LiveResponse>(request, token, `/api/live?route=${encodeURIComponent(routeId)}`)
}

/** `POST /api/incidents/{id}/close`; returns the status and body without asserting. */
export async function closeIncident(
  request: APIRequestContext,
  token: string,
  incidentId: string,
  note: string,
): Promise<{ status: number; body: any }> {
  const res = await request.post(`${BACKEND_URL}/api/incidents/${incidentId}/close`, {
    headers: authHeaders(token),
    data: { note },
  })
  return { status: res.status(), body: await res.json().catch(() => null) }
}

/** `POST /api/routes/{id}/stop`; returns the status and body without asserting. */
export async function stopRoute(
  request: APIRequestContext,
  token: string,
  routeId: string,
  reason: string,
): Promise<{ status: number; body: any }> {
  const res = await request.post(`${BACKEND_URL}/api/routes/${routeId}/stop`, {
    headers: authHeaders(token),
    data: { reason },
  })
  return { status: res.status(), body: await res.json().catch(() => null) }
}
