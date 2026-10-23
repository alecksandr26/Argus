import { test, expect, type APIRequestContext } from '@playwright/test'
import {
  ROOT_ADMIN_EMAIL,
  ROOT_ADMIN_PASSWORD,
  BACKEND_URL,
  FINAL_TWEAKS_UI,
  NEEDS_FINAL_TWEAKS_UI,
  alertEvent,
  authHeaders,
  getAlert,
  getLive,
  login,
  postReportOk,
  reportPoint,
  restLogin,
  setupActiveTruck,
  uniqueSuffix,
  type ActiveTruck,
} from './helpers'

/**
 * Every alert carries its position, labelled when it isn't a live fix (contracts C2.1, C3.2,
 * C3.7): no position at all -> "Position unknown"; the device's saved last good fix -> "Last
 * known (N old)". GPS points can be last-known too, and the route's path is served from them.
 */

async function reportThreeAlerts(request: APIRequestContext, truck: ActiveTruck) {
  const live = await postReportOk(request, truck.deviceKey, {
    points: [],
    event: alertEvent({
      severity: 'critical',
      alert_type: 'eyes_closed',
      age_s: 30,
      position: { lat: 19.1, lon: -98.3, last_known: false, location_age_s: 0 },
      speed: 75,
    }),
  })
  const lastKnown = await postReportOk(request, truck.deviceKey, {
    points: [],
    event: alertEvent({
      severity: 'critical',
      alert_type: 'no_face',
      age_s: 20,
      position: { lat: 19.2, lon: -98.4, last_known: true, location_age_s: 180 },
      speed: null,
      ai_metadata: { model: 'parts-rules-v1', scores: null },
    }),
  })
  const unknown = await postReportOk(request, truck.deviceKey, {
    points: [],
    event: alertEvent({
      severity: 'medium',
      alert_type: 'head_down',
      age_s: 10,
      position: null,
      speed: null,
    }),
  })
  return {
    live: live.event!.id_alert!,
    lastKnown: lastKnown.event!.id_alert!,
    unknown: unknown.event!.id_alert!,
  }
}

test('alerts keep a live, a last-known or no position, as reported', async ({ request }) => {
  const suffix = uniqueSuffix()
  const rootToken = await restLogin(request, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
  const truck = await setupActiveTruck(request, rootToken, suffix, { platePrefix: 'POS' })
  const ids = await reportThreeAlerts(request, truck)

  expect(await getAlert(request, rootToken, ids.live)).toMatchObject({
    coordinates: { lat: 19.1, lon: -98.3 },
    position_unknown: false,
    location_is_last_known: false,
    speed_at_event: 75,
  })
  expect(await getAlert(request, rootToken, ids.lastKnown)).toMatchObject({
    coordinates: { lat: 19.2, lon: -98.4 },
    position_unknown: false,
    location_is_last_known: true,
    location_age_s: 180,
    speed_at_event: null,
  })
  expect(await getAlert(request, rootToken, ids.unknown)).toMatchObject({
    coordinates: null,
    position_unknown: true,
    speed_at_event: null,
  })

  // The incident feed carries every alert with its position too.
  const live = await getLive(request, rootToken, truck.routeId)
  const alerts = live.incidents.flatMap((i) => i.alerts)
  expect(alerts.find((a) => a.id_alert === ids.unknown)?.position_unknown).toBe(true)
  expect(alerts.find((a) => a.id_alert === ids.lastKnown)?.location_is_last_known).toBe(true)
})

test('last-known GPS points are stored as such and drawn as part of the path', async ({
  request,
}) => {
  const suffix = uniqueSuffix()
  const rootToken = await restLogin(request, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
  const truck = await setupActiveTruck(request, rootToken, suffix, { platePrefix: 'POS' })

  // Three live fixes, then the fix is lost: two last-known points (speed 0, aging).
  const result = await postReportOk(request, truck.deviceKey, {
    points: [
      reportPoint({ age_s: 5, lat: 19.0, lon: -98.0 }),
      reportPoint({ age_s: 4, lat: 19.001, lon: -98.001 }),
      reportPoint({ age_s: 3, lat: 19.002, lon: -98.002 }),
      reportPoint({ age_s: 2, lat: 19.002, lon: -98.002, speed: 0, last_known: true, location_age_s: 1, vigilance: 'unknown' }),
      reportPoint({ age_s: 1, lat: 19.002, lon: -98.002, speed: 0, last_known: true, location_age_s: 2, vigilance: 'unknown' }),
    ],
  })
  expect(result.points).toMatchObject({ stored: 5, status: 'ok' })

  const live = await getLive(request, rootToken, truck.routeId)
  expect(live.track.map((p) => p.last_known)).toEqual([false, false, false, true, true])
  expect(live.track[4]).toMatchObject({ location_age_s: 2, vigilance: 'unknown', speed: 0 })
  expect(live.routes[0].latest_position).toMatchObject({ last_known: true, location_age_s: 2 })

  // The full path, for any route status (the route view seeds from it).
  const res = await request.get(`${BACKEND_URL}/api/routes/${truck.routeId}/track`, {
    headers: authHeaders(rootToken),
  })
  expect(res.status()).toBe(200)
  const track = (await res.json()) as { downsampled: boolean; points: { last_known: boolean }[] }
  expect(track.downsampled).toBe(false)
  expect(track.points).toHaveLength(5)
})

test.describe('position labels in the UI', () => {
  test.skip(!FINAL_TWEAKS_UI, NEEDS_FINAL_TWEAKS_UI)

  test('the route view labels last-known and unknown positions, and alert types', async ({
    page,
    request,
  }) => {
    const suffix = uniqueSuffix()
    const rootToken = await restLogin(request, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
    const truck = await setupActiveTruck(request, rootToken, suffix, { platePrefix: 'POS' })
    await reportThreeAlerts(request, truck)

    await page.goto('/login')
    await login(page, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
    await page.goto(`/routes/${truck.routeId}/live`)
    await expect(page.getByText('Position unknown').first()).toBeVisible()
    await expect(page.getByText('Last known (3 min old)').first()).toBeVisible()
    await expect(page.getByText('Eyes closed', { exact: true }).first()).toBeVisible()
    await expect(page.getByText('Driver not visible', { exact: true }).first()).toBeVisible()
    await expect(page.getByText('Head down', { exact: true }).first()).toBeVisible()
  })
})
