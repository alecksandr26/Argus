import { test, expect } from '@playwright/test'
import {
  ROOT_ADMIN_EMAIL,
  ROOT_ADMIN_PASSWORD,
  FINAL_TWEAKS_UI,
  NEEDS_FINAL_TWEAKS_UI,
  alertEvent,
  closeConfirmation,
  closeDialog,
  closeIncident,
  createUserAndLogin,
  getAlert,
  getLive,
  getRoute,
  incidentCard,
  listIncidents,
  login,
  newEpisode,
  okEvent,
  postReport,
  postReportOk,
  restLogin,
  setupActiveTruck,
  tierRegion,
  uniqueSuffix,
} from './helpers'

/**
 * Incidents (contracts C3.4-C3.6), end to end through the real backend: alerts the truck
 * reports through `POST /api/devices/me/report` group into incidents per route (less than
 * 15 min after the incident's last alert, by event time), the route's `incident_state` follows
 * them, the driver's OK never closes anything, and only a guardian or root_admin closes an
 * incident, with a note when it's critical.
 *
 * Event times are relative (`age_s`), so each route here is created as having departed 2 h ago
 * and the alerts are placed in time by their age.
 */

const MIN = 60

test.describe('incident grouping', () => {
  test('alerts group by route within 15 min of the last one, an escalation joins its medium, and the feed is tier-ordered', async ({
    request,
  }) => {
    const suffix = uniqueSuffix()
    const rootToken = await restLogin(request, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
    const truck = await setupActiveTruck(request, rootToken, suffix, {
      departedSecondsAgo: 2 * 60 * MIN,
    })
    const episode = newEpisode()

    // 30 min ago: a medium, then 10 s later its critical escalation (same episode).
    const medium = await postReportOk(request, truck.deviceKey, {
      points: [],
      event: alertEvent({ episode, severity: 'medium', alert_type: 'eyes_closed', age_s: 30 * MIN }),
    })
    const critical = await postReportOk(request, truck.deviceKey, {
      points: [],
      event: alertEvent({ episode, severity: 'critical', alert_type: 'eyes_closed', age_s: 30 * MIN - 10 }),
    })
    expect(medium.event?.status).toBe('created')
    expect(critical.event?.status).toBe('created')
    const mediumAlert = await getAlert(request, rootToken, medium.event!.id_alert!)
    const criticalAlert = await getAlert(request, rootToken, critical.event!.id_alert!)
    expect(criticalAlert.id_incident).toBe(mediumAlert.id_incident)
    expect(criticalAlert.related_alert_id).toBe(mediumAlert.id_alert)

    // Now: 30 min after the last alert, so a new incident.
    const later = await postReportOk(request, truck.deviceKey, {
      points: [],
      event: alertEvent({ severity: 'medium', alert_type: 'head_down', age_s: 0 }),
    })
    const laterAlert = await getAlert(request, rootToken, later.event!.id_alert!)
    expect(laterAlert.id_incident).not.toBe(mediumAlert.id_incident)

    // 14 min before that one (arriving late): within its 15 min window, so it joins it, not
    // the old incident (whose window ended ~15 min ago).
    const late = await postReportOk(request, truck.deviceKey, {
      points: [],
      event: alertEvent({ severity: 'medium', alert_type: 'yawn_eyes_closed', age_s: 14 * MIN }),
    })
    const lateAlert = await getAlert(request, rootToken, late.event!.id_alert!)
    expect(lateAlert.id_incident).toBe(laterAlert.id_incident)

    // GET /api/incidents: newest last_alert_at first.
    const incidents = await listIncidents(request, rootToken, truck.routeId, 'open')
    expect(incidents.map((i) => [i.id_incident, i.severity, i.alert_count])).toEqual([
      [laterAlert.id_incident, 'medium', 2],
      [mediumAlert.id_incident, 'critical', 2],
    ])
    const [newer, older] = incidents
    expect(newer.alerts.map((a) => a.id_alert)).toEqual([lateAlert.id_alert, laterAlert.id_alert])
    expect(newer.truck_plate_number).toBe(truck.plate)
    expect(older.closed_at).toBeNull()

    // /api/live: the feed is tier-ordered (critical before medium), and the route's state is
    // the worst open one.
    const live = await getLive(request, rootToken, truck.routeId)
    expect(live.incidents.map((i) => [i.id_incident, i.tier])).toEqual([
      [mediumAlert.id_incident, 'critical'],
      [laterAlert.id_incident, 'medium'],
    ])
    expect(live.routes[0].incident_state).toBe('critical')
  })

  test('the same event reported twice is one alert, and the driver OK sets driver_ok_at without closing anything', async ({
    request,
  }) => {
    const suffix = uniqueSuffix()
    const rootToken = await restLogin(request, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
    const truck = await setupActiveTruck(request, rootToken, suffix)
    const event = alertEvent({ severity: 'critical', age_s: 60 })

    const first = await postReportOk(request, truck.deviceKey, { points: [], event })
    // A retry after a lost answer, or a dead-letter replay of an event that did get through.
    const again = await postReportOk(request, truck.deviceKey, {
      points: [],
      event: { ...event, age_s: 60, delivery: 'dlq', queued_seconds: 5 },
    })
    expect(first.event?.status).toBe('created')
    expect(again.event).toMatchObject({ status: 'duplicate', id_alert: first.event!.id_alert })
    let [incident] = await listIncidents(request, rootToken, truck.routeId, 'open')
    expect(incident.alert_count).toBe(1)
    expect(incident.driver_ok_at).toBeNull()

    const ok = await postReportOk(request, truck.deviceKey, {
      points: [],
      event: okEvent(event.episode, { age_s: 0 }),
    })
    expect(ok.event).toMatchObject({ status: 'created', id_alert: first.event!.id_alert })

    ;[incident] = await listIncidents(request, rootToken, truck.routeId, 'open')
    expect(incident.driver_ok_at).toBeTruthy()
    expect(incident.closed_at).toBeNull()
    expect((await getRoute(request, rootToken, truck.routeId)).incident_state).toBe('critical')
  })
})

test.describe('closing an incident', () => {
  test('a critical needs a note; guardian and root_admin close, admin gets 403; the route state drops with each close', async ({
    request,
  }) => {
    const suffix = uniqueSuffix()
    const rootToken = await restLogin(request, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
    const truck = await setupActiveTruck(request, rootToken, suffix, {
      departedSecondsAgo: 2 * 60 * MIN,
    })
    const guardian = await createUserAndLogin(request, rootToken, 'guardian', `close-${suffix}`)
    const admin = await createUserAndLogin(request, rootToken, 'admin', `close-${suffix}`)

    // A critical incident 40 min ago, and a medium one now.
    const critical = await postReportOk(request, truck.deviceKey, {
      points: [],
      event: alertEvent({ severity: 'critical', age_s: 40 * MIN }),
    })
    await postReportOk(request, truck.deviceKey, {
      points: [],
      event: alertEvent({ severity: 'medium', alert_type: 'head_down', age_s: 0 }),
    })
    const criticalAlert = await getAlert(request, rootToken, critical.event!.id_alert!)
    const incidents = await listIncidents(request, rootToken, truck.routeId, 'open')
    const criticalIncident = incidents.find((i) => i.severity === 'critical')!
    const mediumIncident = incidents.find((i) => i.severity === 'medium')!
    expect(criticalIncident.id_incident).toBe(criticalAlert.id_incident)
    expect((await getRoute(request, rootToken, truck.routeId)).incident_state).toBe('critical')

    // admin: 403, with or without a note.
    expect((await closeIncident(request, admin.token, criticalIncident.id_incident, 'ok')).status).toBe(403)
    expect((await closeIncident(request, admin.token, mediumIncident.id_incident, '')).status).toBe(403)

    // guardian: a critical with an empty (or blank) note is 422.
    const noNote = await closeIncident(request, guardian.token, criticalIncident.id_incident, '   ')
    expect(noNote.status).toBe(422)
    expect(JSON.stringify(noNote.body)).toContain('A note is required to close a critical incident')

    // guardian, with a note: closed, every alert closed and reviewed; the route drops to the
    // worst remaining open incident (medium).
    const note = `Driver pulled over to rest — ${suffix}`
    const closed = await closeIncident(request, guardian.token, criticalIncident.id_incident, ` ${note} `)
    expect(closed.status).toBe(200)
    expect(closed.body).toMatchObject({ id_incident: criticalIncident.id_incident, close_note: note })
    expect(closed.body.closed_at).toBeTruthy()
    expect(closed.body.closed_by).toBeTruthy()
    for (const a of closed.body.alerts as { closed_at: string | null; reviewed_by_operator: boolean }[]) {
      expect(a.closed_at).toBeTruthy()
      expect(a.reviewed_by_operator).toBe(true)
    }
    expect((await getRoute(request, rootToken, truck.routeId)).incident_state).toBe('medium')

    // Closing twice is 409.
    expect((await closeIncident(request, guardian.token, criticalIncident.id_incident, note)).status).toBe(409)

    // root_admin closes the medium with no note (allowed for a medium): the route is fine.
    const closedMedium = await closeIncident(request, rootToken, mediumIncident.id_incident, '')
    expect(closedMedium.status).toBe(200)
    expect(closedMedium.body.close_note).toBe('')
    expect((await getRoute(request, rootToken, truck.routeId)).incident_state).toBe('fine')
    expect(await listIncidents(request, rootToken, truck.routeId, 'open')).toEqual([])
  })

  test('a late alert never joins a closed incident', async ({ request }) => {
    const suffix = uniqueSuffix()
    const rootToken = await restLogin(request, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
    const truck = await setupActiveTruck(request, rootToken, suffix)

    const first = await postReportOk(request, truck.deviceKey, {
      points: [],
      event: alertEvent({ severity: 'critical', age_s: 120 }),
    })
    const firstAlert = await getAlert(request, rootToken, first.event!.id_alert!)
    expect((await closeIncident(request, rootToken, firstAlert.id_incident!, 'Handled')).status).toBe(200)

    // A replayed critical from 1 min *before* the closed one: well inside its window in time,
    // but the incident is closed, so it opens a new one.
    const replay = await postReport(request, truck.deviceKey, {
      points: [],
      event: alertEvent({ severity: 'critical', age_s: 180, delivery: 'dlq', queued_seconds: 150 }),
    })
    expect(replay.status).toBe(200)
    expect(replay.body.event?.status).toBe('created')
    const replayAlert = await getAlert(request, rootToken, replay.body.event!.id_alert!)
    expect(replayAlert.id_incident).not.toBe(firstAlert.id_incident)
    expect(replayAlert.delivery).toBe('dlq')

    const [open] = await listIncidents(request, rootToken, truck.routeId, 'open')
    expect(open.id_incident).toBe(replayAlert.id_incident)
    expect(open.has_replayed).toBe(true)
  })
})

test.describe('incidents in the UI', () => {
  test.skip(!FINAL_TWEAKS_UI, NEEDS_FINAL_TWEAKS_UI)

  test('Live operations shows the tiers, and a guardian must write a note to close a critical', async ({
    page,
    request,
  }) => {
    const suffix = uniqueSuffix()
    const rootToken = await restLogin(request, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
    const truck = await setupActiveTruck(request, rootToken, suffix, {
      departedSecondsAgo: 3 * 60 * MIN,
    })
    const guardian = await createUserAndLogin(request, rootToken, 'guardian', `ui-${suffix}`)
    const episode = newEpisode()
    // One incident per tier: critical (now), medium (30 min ago), old (2 h ago).
    await postReportOk(request, truck.deviceKey, {
      points: [],
      event: alertEvent({ severity: 'medium', alert_type: 'head_down', age_s: 2 * 60 * MIN }),
    })
    await postReportOk(request, truck.deviceKey, {
      points: [],
      event: alertEvent({ severity: 'medium', alert_type: 'head_down_eyes_closed', age_s: 30 * MIN }),
    })
    await postReportOk(request, truck.deviceKey, {
      points: [],
      event: alertEvent({ episode, severity: 'critical', alert_type: 'eyes_closed', age_s: 60 }),
    })
    await postReportOk(request, truck.deviceKey, {
      points: [],
      event: okEvent(episode, { age_s: 0 }),
    })

    await page.goto('/login')
    await login(page, guardian.email, guardian.password)
    // Live operations: one region per tier (by region name: the header adds a count), and this
    // truck's critical incident in the Critical one.
    await page.goto('/')
    for (const tier of ['Critical', 'Medium', 'Older than 1 h'] as const) {
      await expect(tierRegion(page, tier)).toBeVisible()
    }
    const criticalTier = tierRegion(page, 'Critical')
    await expect(criticalTier.getByText(truck.plate).first()).toBeVisible()
    await expect(incidentCard(criticalTier, 'Eyes closed').first()).toBeVisible()

    // The route view lists this route's three incidents; act on the critical one's own card,
    // whatever order the cards come in.
    await page.goto(`/routes/${truck.routeId}/live`)
    await expect(tierRegion(page, 'Critical')).toBeVisible()
    await expect(tierRegion(page, 'Medium')).toBeVisible()
    await expect(tierRegion(page, 'Older than 1 h')).toBeVisible()
    const criticalCard = incidentCard(page, 'Eyes closed')
    await expect(criticalCard).toBeVisible()
    await expect(incidentCard(page, 'Head down, eyes closed')).toBeVisible()
    await expect(incidentCard(page, 'Head down')).toBeVisible()
    await expect(criticalCard.getByText(/Driver OK since \d{1,2}:\d{2}/)).toBeVisible()

    await criticalCard.getByRole('button', { name: 'Close incident' }).click()
    const dialog = closeDialog(page)
    await dialog.getByRole('button', { name: 'Close incident' }).click()
    await expect(page.getByText('A note is required to close a critical incident')).toBeVisible()
    await dialog.getByRole('textbox').fill(`Rest stop agreed — ${suffix}`)
    await dialog.getByRole('button', { name: 'Close incident' }).click()
    await expect(dialog).toBeHidden()
    await expect(closeConfirmation(page)).toBeVisible()

    // Only the critical left the Open list; both mediums are still open.
    await expect(incidentCard(page, 'Eyes closed')).toHaveCount(0)
    await expect(incidentCard(page, 'Head down, eyes closed')).toBeVisible()
    await expect(incidentCard(page, 'Head down')).toBeVisible()
    const open = await listIncidents(request, rootToken, truck.routeId, 'open')
    expect(open.map((i) => i.severity).sort()).toEqual(['medium', 'medium'])
  })
})
