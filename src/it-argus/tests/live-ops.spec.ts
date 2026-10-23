import { test, expect } from '@playwright/test'
import {
  ROOT_ADMIN_EMAIL,
  ROOT_ADMIN_PASSWORD,
  BACKEND_URL,
  CLOSED_AT,
  FINAL_TWEAKS_UI,
  NEEDS_FINAL_TWEAKS_UI,
  alertEvent,
  authHeaders,
  closeConfirmation,
  closeDialog,
  closeIncident,
  createDriver,
  createTruck,
  createUserAndLogin,
  getAlert,
  incidentCard,
  listIncidents,
  login,
  postReportOk,
  reportPoint,
  restLogin,
  rotateDeviceKey,
  setupActiveTruck,
  tierRegion,
  uniqueSuffix,
} from './helpers'

/**
 * Live operations (`LiveOps.tsx`) and the route/alert view (`RouteLiveView.tsx`), fed the way a
 * real truck feeds them: every alert here is reported by the truck itself through
 * `POST /api/devices/me/report` with its own device key (contracts C2), the only device write
 * after the final tweaks. The panic button is gone, and so is the per-alert "review": a guardian
 * closes the alert's *incident*, with a note when it's critical (C3.6).
 *
 * The UI half of some tests needs ui-argus's final-tweaks screens (incident feed, close dialog,
 * alert-type labels), which merge after this spec; those parts are skipped unless
 * `IT_FINAL_TWEAKS_UI=1` (see helpers.ts). The API checks always run.
 */

test('root_admin creates a route via the Routes screen, and a critical alert the truck reports shows live on the dashboard', async ({
  page,
  request,
}) => {
  const suffix = uniqueSuffix()
  const plate = `LIVE-${suffix}`
  const origin = `Origin-${suffix}`
  const destination = `Dest-${suffix}`

  const rootToken = await restLogin(request, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
  const truck = await createTruck(request, rootToken, plate)
  const driver = await createDriver(request, rootToken, `LIC-${suffix}`)

  // The route itself goes through the real Routes screen (TravelManagement.tsx).
  await page.goto('/login')
  await login(page, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
  await page.goto('/routes')
  await page.getByLabel('Driver').selectOption(driver.id_driver)
  await page.getByLabel('Truck').selectOption(truck.id_truck)
  await page.getByLabel('Origin').fill(origin)
  await page.getByLabel('Destination').fill(destination)
  await page.getByRole('button', { name: 'Create route' }).click()
  await expect(page.getByText(`${origin} → ${destination}`, { exact: true })).toBeVisible()

  // The Routes screen creates the route `scheduled`; "start the trip" over REST, which sets
  // `actual_departure = now` (C3.1): device events are matched to the route by that time.
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

  // The truck reports a GPS point and a critical "eyes closed" alert in one report.
  const deviceKey = await rotateDeviceKey(request, rootToken, truck.id_truck)
  const result = await postReportOk(request, deviceKey, {
    points: [reportPoint({ age_s: 1, vigilance: 'critical' })],
    event: alertEvent({ severity: 'critical', alert_type: 'eyes_closed' }),
  })
  expect(result.points).toMatchObject({ stored: 1, route: routeId, status: 'ok' })
  expect(result.event).toMatchObject({ status: 'created', reason: null })
  expect(result.active_route).toBe(routeId)

  const alert = await getAlert(request, rootToken, result.event!.id_alert!)
  expect(alert).toMatchObject({
    id_route: routeId,
    source: 'fusion',
    severity_level: 'critical',
    alert_type: 'eyes_closed',
    delivery: 'live',
    position_unknown: false,
  })
  expect(alert.id_incident).toBeTruthy()

  // Live operations: the truck's marker is on the map.
  await page.goto('/')
  await expect(page.locator('.fleet-marker__plate', { hasText: plate })).toBeVisible()

  if (FINAL_TWEAKS_UI) {
    // The incident feed: the Critical tier holds this truck's incident, labelled by alert type.
    const criticalTier = tierRegion(page, 'Critical')
    await expect(criticalTier.getByText(plate).first()).toBeVisible()
    await expect(incidentCard(criticalTier, 'Eyes closed').first()).toBeVisible()

    // The route view lists the incident as open: its own card (not the `Open` filter chip)
    // still offers Close incident to root_admin and carries no `Closed HH:MM` pill.
    await page.goto(`/routes/${routeId}/live`)
    const card = incidentCard(page, 'Eyes closed')
    await expect(card).toBeVisible()
    await expect(card.getByRole('button', { name: 'Close incident' })).toBeVisible()
    await expect(card.getByText(CLOSED_AT)).toHaveCount(0)
  }
})

test('a no-face critical reported by the truck carries no scores, and a guardian closes its incident with a note', async ({
  page,
  request,
}) => {
  const suffix = uniqueSuffix()
  const rootToken = await restLogin(request, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
  const truck = await setupActiveTruck(request, rootToken, suffix, { platePrefix: 'LIVE' })
  const guardian = await createUserAndLogin(request, rootToken, 'guardian', `liveops-${suffix}`)
  const note = `Called the driver, camera was blocked by a sun visor — ${suffix}`

  // What replaced the panic test: an alert the truck itself reports. No face means no model
  // output, so the scores are null (C1.5 / C2.1).
  const result = await postReportOk(request, truck.deviceKey, {
    points: [reportPoint({ age_s: 2, vigilance: 'critical' })],
    event: alertEvent({
      severity: 'critical',
      alert_type: 'no_face',
      ai_metadata: { model: 'parts-rules-v1', scores: null },
    }),
  })
  expect(result.event?.status).toBe('created')
  const alert = await getAlert(request, rootToken, result.event!.id_alert!)
  expect(alert.alert_type).toBe('no_face')
  expect(alert.source).toBe('fusion')
  const [incident] = await listIncidents(request, rootToken, truck.routeId, 'open')
  expect(incident.id_incident).toBe(alert.id_incident)

  if (FINAL_TWEAKS_UI) {
    await page.goto('/login')
    await login(page, guardian.email, guardian.password)
    await page.goto(`/routes/${truck.routeId}/live`)
    // The incident is open: its card is listed, with its own Close incident button and no
    // `Closed HH:MM` pill (the `Open` filter chip says nothing about the incident itself).
    const card = incidentCard(page, 'Driver not visible')
    await expect(card).toBeVisible()
    await expect(card.getByText(CLOSED_AT)).toHaveCount(0)

    // A critical incident can't be closed without a note.
    await card.getByRole('button', { name: 'Close incident' }).click()
    const dialog = closeDialog(page)
    await dialog.getByRole('button', { name: 'Close incident' }).click()
    await expect(
      page.getByText('A note is required to close a critical incident'),
    ).toBeVisible()
    await dialog.getByRole('textbox').fill(note)
    await dialog.getByRole('button', { name: 'Close incident' }).click()
    await expect(dialog).toBeHidden()
    await expect(closeConfirmation(page)).toBeVisible()
    await expect(incidentCard(page, 'Driver not visible')).toHaveCount(0)

    // It round-tripped through the backend, not just local state: after a reload the incident
    // is under the Closed filter, with its close time and note.
    await page.reload()
    await page.getByRole('button', { name: 'Closed', exact: true }).click()
    const closedCard = incidentCard(page, 'Driver not visible')
    await expect(closedCard.getByText(CLOSED_AT)).toBeVisible()
    await expect(closedCard.getByText(note)).toBeVisible()
  } else {
    // Without the new UI, close it over REST as the same guardian.
    const closed = await closeIncident(request, guardian.token, incident.id_incident, note)
    expect(closed.status).toBe(200)
  }

  const [closedIncident] = await listIncidents(request, rootToken, truck.routeId, 'closed')
  expect(closedIncident.id_incident).toBe(incident.id_incident)
  expect(closedIncident.close_note).toBe(note)
  const closedAlert = await getAlert(request, rootToken, alert.id_alert)
  expect(closedAlert.closed_at).toBeTruthy()
  expect(closedAlert.reviewed_by_operator).toBe(true)
})

test('an admin sees open incidents but cannot close them', async ({ page, request }) => {
  const suffix = uniqueSuffix()
  const rootToken = await restLogin(request, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
  const truck = await setupActiveTruck(request, rootToken, suffix, { platePrefix: 'LIVE' })
  const admin = await createUserAndLogin(request, rootToken, 'admin', `liveops-${suffix}`)

  await postReportOk(request, truck.deviceKey, {
    points: [],
    event: alertEvent({ severity: 'medium', alert_type: 'head_down' }),
  })
  const [incident] = await listIncidents(request, admin.token, truck.routeId, 'open')
  expect(incident.severity).toBe('medium')

  // Closing is guardian + root_admin only (C3.6): an admin gets 403, even with a note.
  const res = await closeIncident(request, admin.token, incident.id_incident, 'Looks fine')
  expect(res.status).toBe(403)
  const [stillOpen] = await listIncidents(request, rootToken, truck.routeId, 'open')
  expect(stillOpen.id_incident).toBe(incident.id_incident)

  if (FINAL_TWEAKS_UI) {
    await page.goto('/login')
    await login(page, admin.email, admin.password)
    await page.goto(`/routes/${truck.routeId}/live`)
    await expect(page.getByText('Head down', { exact: true }).first()).toBeVisible()
    await expect(page.getByRole('button', { name: /close incident/i })).toHaveCount(0)
  }
})

test('a guardian de-escalates a critical alert, and the original severity and reason persist', async ({
  page,
  request,
}) => {
  const suffix = uniqueSuffix()
  const reason = `Called driver, eyes were fine — ${suffix}`
  const rootToken = await restLogin(request, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
  const truck = await setupActiveTruck(request, rootToken, suffix, { platePrefix: 'LIVE' })
  const guardian = await createUserAndLogin(request, rootToken, 'guardian', `deesc-${suffix}`)

  const result = await postReportOk(request, truck.deviceKey, {
    points: [reportPoint({ vigilance: 'critical' })],
    event: alertEvent({ severity: 'critical', alert_type: 'eyes_closed' }),
  })
  const alertId = result.event!.id_alert!

  await page.goto('/login')
  await login(page, guardian.email, guardian.password)
  await page.goto(`/alerts/${alertId}`)
  await expect(page.getByText('CRITICAL SEVERITY', { exact: true })).toBeVisible()

  const submit = page.getByRole('button', { name: 'De-escalate', exact: true })
  await expect(submit).toBeDisabled()
  await page.getByLabel('New severity').selectOption('medium')
  await page.getByLabel('De-escalation reason').fill(reason)
  await submit.click()

  await expect(page.getByText('MEDIUM SEVERITY', { exact: true })).toBeVisible()
  await expect(page.getByTestId('deescalation-note')).toContainText(
    'De-escalated from Critical to Medium',
  )
  await expect(page.getByTestId('deescalation-note')).toContainText(reason)

  // Survives a reload (real backend round trip).
  await page.reload()
  await expect(page.getByText('MEDIUM SEVERITY', { exact: true })).toBeVisible()
  await expect(page.getByTestId('deescalation-note')).toContainText(reason)

  // De-escalating counts as a review, and recomputes the incident's severity (C3.4); the
  // incident stays open (closing is a separate, explicit step).
  const alert = await getAlert(request, rootToken, alertId)
  expect(alert.severity_level).toBe('medium')
  expect(alert.reviewed_by_operator).toBe(true)
  const [incident] = await listIncidents(request, rootToken, truck.routeId, 'open')
  expect(incident.id_incident).toBe(alert.id_incident)
  expect(incident.severity).toBe('medium')
})

test.describe('final-tweaks UI', () => {
  test.skip(!FINAL_TWEAKS_UI, NEEDS_FINAL_TWEAKS_UI)

  test('a replayed critical reads "Recovered after lost connection" in the feed', async ({
    page,
    request,
  }) => {
    const suffix = uniqueSuffix()
    const rootToken = await restLogin(request, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
    const truck = await setupActiveTruck(request, rootToken, suffix, { platePrefix: 'LIVE' })
    await postReportOk(request, truck.deviceKey, {
      points: [],
      event: alertEvent({ age_s: 300, delivery: 'dlq', queued_seconds: 240 }),
    })

    await page.goto('/login')
    await login(page, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
    await page.goto(`/routes/${truck.routeId}/live`)
    await expect(page.getByText('Recovered after lost connection').first()).toBeVisible()
  })
})
