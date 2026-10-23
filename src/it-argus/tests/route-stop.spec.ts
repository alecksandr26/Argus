import { test, expect } from '@playwright/test'
import {
  ROOT_ADMIN_EMAIL,
  ROOT_ADMIN_PASSWORD,
  BACKEND_URL,
  FINAL_TWEAKS_UI,
  NEEDS_FINAL_TWEAKS_UI,
  alertEvent,
  authHeaders,
  createUserAndLogin,
  getLive,
  getRoute,
  listIncidents,
  login,
  okEvent,
  postReportOk,
  reportPoint,
  restLogin,
  setupActiveTruck,
  stopRoute,
  uniqueSuffix,
} from './helpers'

/**
 * "Stop route" (contracts C3.6): root_admin/admin end a route early with a required reason. From
 * then on the backend rejects everything the truck sends for it: its points get
 * `points.status: route_finished` and are not stored, and any event that happened after the
 * stop, or during the route but delivered after it, is `rejected route_finished` (C2.2 rule 4).
 * Open incidents stay open. "Complete trip" ends a route the same way for the device.
 */

test('stopping a route needs a reason and the right role, and then the truck reports are rejected', async ({
  request,
}) => {
  const suffix = uniqueSuffix()
  const rootToken = await restLogin(request, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
  const truck = await setupActiveTruck(request, rootToken, suffix)
  const guardian = await createUserAndLogin(request, rootToken, 'guardian', `stop-${suffix}`)
  const admin = await createUserAndLogin(request, rootToken, 'admin', `stop-${suffix}`)

  // While the route runs: points are stored on it, and a critical opens an incident.
  const critical = alertEvent({ severity: 'critical', age_s: 2 })
  const before = await postReportOk(request, truck.deviceKey, {
    points: [reportPoint({ age_s: 3 }), reportPoint({ age_s: 2, vigilance: 'critical' })],
    event: critical,
  })
  expect(before.points).toMatchObject({ stored: 2, route: truck.routeId, status: 'ok' })
  expect(before.event?.status).toBe('created')
  expect(before.active_route).toBe(truck.routeId)

  // Roles and reason: guardian 403; an empty reason 422; admin with a reason succeeds.
  expect((await stopRoute(request, guardian.token, truck.routeId, 'Breakdown')).status).toBe(403)
  const blank = await stopRoute(request, admin.token, truck.routeId, '   ')
  expect(blank.status).toBe(422)
  expect(JSON.stringify(blank.body)).toContain('A reason is required to stop a route')

  const reason = `Engine fault near Puebla — ${suffix}`
  const stopped = await stopRoute(request, admin.token, truck.routeId, reason)
  expect(stopped.status).toBe(200)
  expect(stopped.body).toMatchObject({
    id_route: truck.routeId,
    operative_status: 'cancelled',
    stop_reason: reason,
  })
  expect(stopped.body.stopped_by).toBeTruthy()
  expect(stopped.body.stopped_at).toBeTruthy()
  expect(stopped.body.actual_arrival).toBeTruthy()
  expect((await stopRoute(request, rootToken, truck.routeId, 'again')).status).toBe(409)

  // After the stop: the points are refused, and so is an event that happened after it.
  const after = await postReportOk(request, truck.deviceKey, {
    points: [reportPoint({ age_s: 0 })],
    event: alertEvent({ severity: 'medium', alert_type: 'head_down', age_s: 0 }),
  })
  expect(after.points).toMatchObject({ stored: 0, route: null, status: 'route_finished' })
  expect(after.event).toMatchObject({ status: 'rejected', reason: 'route_finished' })
  expect(after.active_route).toBeNull()

  // The driver's OK for that critical, after the stop, is rejected the same way (C2.2 [sync 1]).
  const lateOk = await postReportOk(request, truck.deviceKey, {
    points: [],
    event: okEvent(critical.episode, { age_s: 0 }),
  })
  expect(lateOk.event).toMatchObject({ status: 'rejected', reason: 'route_finished' })

  // A dead-letter replay of an event from *during* the route, delivered after the stop: still
  // rejected route_finished (the device archives it).
  const replay = await postReportOk(request, truck.deviceKey, {
    points: [],
    event: alertEvent({ severity: 'critical', age_s: 600, delivery: 'dlq', queued_seconds: 590 }),
  })
  expect(replay.event).toMatchObject({ status: 'rejected', reason: 'route_finished' })

  // The open incident from before the stop stays open, flagged as on an ended route.
  const [incident] = await listIncidents(request, rootToken, truck.routeId, 'open')
  expect(incident.alert_count).toBe(1)
  expect(incident.route_ended).toBe(true)

  // The route's GPS path is the two points from before the stop.
  const live = await getLive(request, rootToken, truck.routeId)
  expect(live.routes[0].operative_status).toBe('cancelled')
  expect(live.track).toHaveLength(2)
})

test('completing a trip ends the route for the device the same way', async ({ request }) => {
  const suffix = uniqueSuffix()
  const rootToken = await restLogin(request, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
  const truck = await setupActiveTruck(request, rootToken, suffix)

  const put = await request.put(`${BACKEND_URL}/api/routes/${truck.routeId}`, {
    headers: authHeaders(rootToken),
    data: { operative_status: 'completed' },
  })
  expect(put.status()).toBe(200)
  expect((await getRoute(request, rootToken, truck.routeId)).actual_arrival).toBeTruthy()

  const after = await postReportOk(request, truck.deviceKey, {
    points: [reportPoint({ age_s: 0 })],
    event: alertEvent({ age_s: 0 }),
  })
  expect(after.points.status).toBe('route_finished')
  expect(after.event).toMatchObject({ status: 'rejected', reason: 'route_finished' })

  // And a finished route can't be restarted (C3.1 [sync 2]).
  const reopen = await request.put(`${BACKEND_URL}/api/routes/${truck.routeId}`, {
    headers: authHeaders(rootToken),
    data: { operative_status: 'in_progress' },
  })
  expect(reopen.status()).toBe(409)
})

test('a truck that never started a route gets no_route, not route_finished', async ({
  request,
}) => {
  // C2.2 rule 4 [sync 2]: `no_route` means no route had started yet when the event happened.
  const suffix = uniqueSuffix()
  const rootToken = await restLogin(request, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
  const truck = await setupActiveTruck(request, rootToken, suffix, { departedSecondsAgo: 60 })

  const result = await postReportOk(request, truck.deviceKey, {
    points: [],
    event: alertEvent({ age_s: 600 }), // 10 min ago: before this truck's only route departed
  })
  expect(result.event).toMatchObject({ status: 'rejected', reason: 'no_route' })
  expect(result.active_route).toBe(truck.routeId)
})

test.describe('stopping a route in the UI', () => {
  test.skip(!FINAL_TWEAKS_UI, NEEDS_FINAL_TWEAKS_UI)

  test('the Stop route dialog requires a reason, and the route then reads Stopped', async ({
    page,
    request,
  }) => {
    const suffix = uniqueSuffix()
    const rootToken = await restLogin(request, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
    const truck = await setupActiveTruck(request, rootToken, suffix)

    await page.goto('/login')
    await login(page, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
    await page.goto(`/routes/${truck.routeId}/live`)
    await page.getByRole('button', { name: 'Stop route' }).first().click()
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('button', { name: 'Stop route' }).click()
    await expect(page.getByText('A reason is required to stop a route')).toBeVisible()
    await dialog.getByRole('textbox').fill(`Driver unwell — ${suffix}`)
    await dialog.getByRole('button', { name: 'Stop route' }).click()
    await expect(page.getByText('Stopped', { exact: true }).first()).toBeVisible()

    const route = await getRoute(request, rootToken, truck.routeId)
    expect(route.operative_status).toBe('cancelled')
    expect(route.stop_reason).toBe(`Driver unwell — ${suffix}`)
  })
})
