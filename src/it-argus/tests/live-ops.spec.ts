import { test, expect, type APIRequestContext } from '@playwright/test'
import {
  ROOT_ADMIN_EMAIL,
  ROOT_ADMIN_PASSWORD,
  BACKEND_URL,
  login,
  restLogin,
  authHeaders,
  sha256Hex,
  uniqueSuffix,
} from './helpers'

/**
 * Exercises the two screens neither `it-argus`'s own auth/users/trucks/drivers specs nor
 * `ui-argus`'s Vitest suite touch: Live operations (`LiveOps.tsx`) and Alert triage
 * (`AlertTriage.tsx`). Real ESP32 firmware doesn't exist yet (root CLAUDE.md), so
 * `Status_Route`/`Alert` records here are POSTed directly against the backend using a
 * root_admin JWT — the exact fallback `authorize_device_or_user()` documents for manual
 * testing/demo seeding without real hardware (see backend-argus's CLAUDE.md's "Device (ESP32)
 * auth"), the same path `scripts/seed_dev_data.py` and `src/simulator-argus` use. The route
 * itself is created through the real Routes screen (`TravelManagement.tsx`) for the one test
 * that exercises it — that screen always creates a route as `scheduled`, so it's promoted to
 * `in_progress` afterward via a REST `PUT`, standing in for a "trip started" event this UI has
 * no control for yet.
 */

async function createTruck(request: APIRequestContext, token: string, plate: string) {
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
  expect(res.status()).toBe(201)
  return (await res.json()) as { id_truck: string; plate_number: string }
}

async function createDriver(request: APIRequestContext, token: string, license: string) {
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
  expect(res.status()).toBe(201)
  return (await res.json()) as { id_driver: string; first_name: string; last_name: string }
}

async function createRouteRest(
  request: APIRequestContext,
  token: string,
  opts: { driverId: string; truckId: string; origin: string; destination: string },
) {
  const res = await request.post(`${BACKEND_URL}/api/routes`, {
    headers: authHeaders(token),
    data: {
      id_driver: opts.driverId,
      id_truck: opts.truckId,
      origin_name: opts.origin,
      destination_name: opts.destination,
      destination_coordinates: { lat: 19.04, lon: -98.2 },
      estimated_departure: new Date().toISOString(),
      operative_status: 'in_progress',
    },
  })
  expect(res.status()).toBe(201)
  return (await res.json()) as { id_route: string }
}

async function postStatus(
  request: APIRequestContext,
  token: string,
  routeId: string,
  vigilance: 'low' | 'medium' | 'critical',
) {
  const res = await request.post(`${BACKEND_URL}/api/routes/${routeId}/status`, {
    headers: authHeaders(token),
    data: {
      current_coordinates: { lat: 19.05, lon: -98.25 },
      current_speed: 85,
      odometer: 1234.5,
      vigilance,
    },
  })
  expect(res.status()).toBe(201)
  return await res.json()
}

async function postFusionAlert(
  request: APIRequestContext,
  token: string,
  routeId: string,
  opts: { alertType: string; severity: 'low' | 'medium' | 'critical' },
) {
  const res = await request.post(`${BACKEND_URL}/api/alerts`, {
    headers: authHeaders(token),
    data: {
      id_route: routeId,
      alert_type: opts.alertType,
      severity_level: opts.severity,
      source: 'fusion',
      ai_metadata: { scores: { not_drowsy: 0.12, drowsy: 0.88 } },
      grip_status: 'bad',
      coordinates: { lat: 19.05, lon: -98.25 },
      speed_at_event: 82,
    },
  })
  expect(res.status()).toBe(201)
  return (await res.json()) as { id_alert: string }
}

async function postPanicButtonAlert(
  request: APIRequestContext,
  token: string,
  routeId: string,
  opts: { alertType: string; severity: 'low' | 'medium' | 'critical' },
) {
  const res = await request.post(`${BACKEND_URL}/api/alerts`, {
    headers: authHeaders(token),
    data: {
      id_route: routeId,
      alert_type: opts.alertType,
      severity_level: opts.severity,
      source: 'panic_button',
      coordinates: { lat: 19.05, lon: -98.25 },
      speed_at_event: 60,
    },
  })
  expect(res.status()).toBe(201)
  return (await res.json()) as { id_alert: string }
}

async function createUserRest(
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
  expect(res.status()).toBe(201)
}

test('root_admin creates a route via the Routes screen, and a fused critical alert shows live on the dashboard and in triage', async ({
  page,
  request,
}) => {
  const suffix = uniqueSuffix()
  const plate = `LIVE-${suffix}`
  const license = `LIC-${suffix}`
  const origin = `Origin-${suffix}`
  const destination = `Dest-${suffix}`
  const alertType = `Fused drowsiness event ${suffix}`

  const rootToken = await restLogin(request, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
  const truck = await createTruck(request, rootToken, plate)
  const driver = await createDriver(request, rootToken, license)

  // The route itself goes through the real Routes screen — this is the one test in this suite
  // that exercises TravelManagement.tsx, since the other tests below are really about
  // LiveOps/AlertTriage rather than route creation.
  await page.goto('/login')
  await login(page, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
  await page.goto('/routes')
  await page.getByLabel('Driver').selectOption(driver.id_driver)
  await page.getByLabel('Truck').selectOption(truck.id_truck)
  await page.getByLabel('Origin').fill(origin)
  await page.getByLabel('Destination').fill(destination)
  await page.getByRole('button', { name: 'Create route' }).click()
  await expect(page.getByText(`${origin} → ${destination}`, { exact: true })).toBeVisible()

  // TravelManagement.tsx always creates a route as `scheduled` (OSRM-driven confirm is
  // deferred, per that file's own comment) — promote it to `in_progress` via REST, standing in
  // for a "trip started" event, so it shows up in `GET /api/routes/active`.
  const listRes = await request.get(`${BACKEND_URL}/api/routes`, {
    headers: authHeaders(rootToken),
  })
  const routes = (await listRes.json()) as { id_route: string; origin_name: string }[]
  const route = routes.find((r) => r.origin_name === origin)
  expect(route).toBeTruthy()
  const routeId = route!.id_route
  const putRes = await request.put(`${BACKEND_URL}/api/routes/${routeId}`, {
    headers: authHeaders(rootToken),
    data: { operative_status: 'in_progress' },
  })
  expect(putRes.status()).toBe(200)

  await postStatus(request, rootToken, routeId, 'critical')
  const alert = await postFusionAlert(request, rootToken, routeId, {
    alertType,
    severity: 'critical',
  })

  // Live operations: the map marker, the alert feed entry, and severity filtering.
  await page.goto('/')
  await expect(
    page.locator('.fleet-marker__plate', { hasText: plate }),
  ).toBeVisible()
  await expect(page.getByText(alertType, { exact: true })).toBeVisible()

  await page.getByRole('button', { name: 'Low' }).click()
  await expect(page.getByText(alertType, { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Critical' }).click()
  await expect(page.getByText(alertType, { exact: true })).toBeVisible()

  // Clicking through to Alert triage — full detail rendering for a `fusion` alert.
  await page.getByText(alertType, { exact: true }).click()
  await expect(page).toHaveURL(`/alerts/${alert.id_alert}`)
  await expect(page.getByRole('heading', { name: alertType, exact: true })).toBeVisible()
  await expect(page.getByText('CRITICAL SEVERITY', { exact: true })).toBeVisible()
  await expect(page.getByText('Camera + grip sensor (fused)')).toBeVisible()
  await expect(page.getByText('Not drowsy', { exact: true })).toBeVisible()
  await expect(page.getByText('Drowsy', { exact: true })).toBeVisible()
  await expect(page.getByText('88%')).toBeVisible()
  await expect(page.getByText('Steering-wheel grip')).toBeVisible()
})

test('a panic_button alert shows no AI scores, and a guardian can review it and have the review persist', async ({
  page,
  request,
}) => {
  const suffix = uniqueSuffix()
  const alertType = `Panic button ${suffix}`
  const guardianEmail = `guardian-liveops-${suffix}@argus.dev`
  const guardianPassword = 'guardian-pass-123'
  const noteText = `Checked in with driver, false alarm — ${suffix}`

  const rootToken = await restLogin(request, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
  const truck = await createTruck(request, rootToken, `LIVE-${suffix}`)
  const driver = await createDriver(request, rootToken, `LIC-${suffix}`)
  const route = await createRouteRest(request, rootToken, {
    driverId: driver.id_driver,
    truckId: truck.id_truck,
    origin: `Origin-${suffix}`,
    destination: `Dest-${suffix}`,
  })
  await postStatus(request, rootToken, route.id_route, 'critical')
  const alert = await postPanicButtonAlert(request, rootToken, route.id_route, {
    alertType,
    severity: 'critical',
  })
  await createUserRest(request, rootToken, {
    email: guardianEmail,
    password: guardianPassword,
    role: 'guardian',
  })

  await page.goto('/login')
  await login(page, guardianEmail, guardianPassword)
  await page.goto(`/alerts/${alert.id_alert}`)

  await expect(page.getByRole('heading', { name: alertType, exact: true })).toBeVisible()
  await expect(page.getByText('CRITICAL SEVERITY', { exact: true })).toBeVisible()
  await expect(page.getByText('Trigger source')).toBeVisible()
  await expect(page.getByText('Driver-activated panic button', { exact: true })).toBeVisible()
  // No camera/grip evaluation happens for a panic_button alert — the score bars/labels
  // `Model output` would render for a `fusion` alert must be absent here.
  await expect(page.getByText('Not drowsy', { exact: true })).toHaveCount(0)
  await expect(page.getByText('Drowsy', { exact: true })).toHaveCount(0)

  // Guardian is one of the two roles allowed to review (root_admin, guardian) — review, save,
  // then reload to prove it round-tripped through the real backend, not just local state.
  await page.getByLabel('Mark alert as reviewed').check()
  await page.getByPlaceholder('Operator notes — what was observed, what action was taken…').fill(
    noteText,
  )
  await page.getByRole('button', { name: 'Save and close alert' }).click()
  await expect(page.getByRole('button', { name: 'Saved' })).toBeVisible()

  await page.reload()
  await expect(page.getByLabel('Mark alert as reviewed')).toBeChecked()
  await expect(
    page.getByPlaceholder('Operator notes — what was observed, what action was taken…'),
  ).toHaveValue(noteText)
})

test('an admin sees alert triage read-only, with no review controls', async ({
  page,
  request,
}) => {
  const suffix = uniqueSuffix()
  const alertType = `Ops check ${suffix}`
  const adminEmail = `operator-liveops-${suffix}@argus.dev`
  const adminPassword = 'operator-pass-123'

  const rootToken = await restLogin(request, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
  const truck = await createTruck(request, rootToken, `LIVE-${suffix}`)
  const driver = await createDriver(request, rootToken, `LIC-${suffix}`)
  const route = await createRouteRest(request, rootToken, {
    driverId: driver.id_driver,
    truckId: truck.id_truck,
    origin: `Origin-${suffix}`,
    destination: `Dest-${suffix}`,
  })
  await postStatus(request, rootToken, route.id_route, 'medium')
  const alert = await postFusionAlert(request, rootToken, route.id_route, {
    alertType,
    severity: 'medium',
  })
  await createUserRest(request, rootToken, {
    email: adminEmail,
    password: adminPassword,
    role: 'admin',
  })

  await page.goto('/login')
  await login(page, adminEmail, adminPassword)
  await page.goto(`/alerts/${alert.id_alert}`)

  await expect(page.getByRole('heading', { name: alertType, exact: true })).toBeVisible()
  await expect(page.getByText('MEDIUM SEVERITY', { exact: true })).toBeVisible()
  await expect(page.getByText('Not yet reviewed')).toBeVisible()

  // `canReview` is root_admin/guardian only (`AlertTriage.tsx`) — an admin gets none of these.
  await expect(page.getByText('Immediate actions')).toHaveCount(0)
  await expect(page.getByLabel('Mark alert as reviewed')).toHaveCount(0)
  await expect(
    page.getByPlaceholder('Operator notes — what was observed, what action was taken…'),
  ).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Save and close alert' })).toHaveCount(0)
})
