import { describe, it, expect } from 'vitest'
import {
  canCloseIncidents,
  canEndRoutes,
  canStopRoute,
  closeNoteError,
  groupIncidentFeed,
  newestAlert,
  sortIncidents,
  stopReasonError,
} from './incidents'
import { at, makeAlert, makeIncident } from '../test/fixtures'
import type { Incident } from '../types'

const ids = (is: Incident[]) => is.map((i) => i.id_incident)

describe('sortIncidents', () => {
  it('orders by tier Critical → Medium → Old, newest last_alert_at first within a tier', () => {
    const incidents = [
      makeIncident({ id_incident: 'old-new', tier: 'old', last_alert_at: at(50) }),
      makeIncident({ id_incident: 'med-old', tier: 'medium', last_alert_at: at(10) }),
      makeIncident({ id_incident: 'crit', tier: 'critical', severity: 'critical', last_alert_at: at(5) }),
      makeIncident({ id_incident: 'med-new', tier: 'medium', last_alert_at: at(40) }),
      makeIncident({ id_incident: 'old-old', tier: 'old', last_alert_at: at(1) }),
    ]
    expect(ids(sortIncidents(incidents))).toEqual(['crit', 'med-new', 'med-old', 'old-new', 'old-old'])
  })

  it('puts an old critical incident in the Old tier (the server tier wins over severity)', () => {
    const incidents = [
      makeIncident({ id_incident: 'old-crit', tier: 'old', severity: 'critical', last_alert_at: at(90) }),
      makeIncident({ id_incident: 'med', tier: 'medium', last_alert_at: at(1) }),
    ]
    expect(ids(sortIncidents(incidents))).toEqual(['med', 'old-crit'])
  })

  it('does not mutate its input', () => {
    const incidents = [
      makeIncident({ id_incident: 'b', tier: 'old' }),
      makeIncident({ id_incident: 'a', tier: 'critical' }),
    ]
    sortIncidents(incidents)
    expect(ids(incidents)).toEqual(['b', 'a'])
  })
})

describe('groupIncidentFeed', () => {
  it('builds tier sections, leaving out empty tiers', () => {
    const feed = groupIncidentFeed([
      makeIncident({ id_incident: 'o', tier: 'old' }),
      makeIncident({ id_incident: 'c', tier: 'critical', severity: 'critical' }),
    ])
    expect(feed.map((s) => s.tier)).toEqual(['critical', 'old'])
    expect(feed.map((s) => s.count)).toEqual([1, 1])
  })

  it('groups a tier by route, routes ordered by their newest incident, incidents newest first', () => {
    const feed = groupIncidentFeed([
      makeIncident({ id_incident: 'r1-a', id_route: 'r-1', tier: 'medium', last_alert_at: at(10) }),
      makeIncident({ id_incident: 'r2-a', id_route: 'r-2', tier: 'medium', last_alert_at: at(30),
        truck_plate_number: 'ARG-002', driver_full_name: 'Luis' }),
      makeIncident({ id_incident: 'r1-b', id_route: 'r-1', tier: 'medium', last_alert_at: at(20) }),
    ])
    expect(feed).toHaveLength(1)
    const [section] = feed
    expect(section.count).toBe(3)
    expect(section.routes.map((r) => r.id_route)).toEqual(['r-2', 'r-1'])
    expect(section.routes[0].truck_plate_number).toBe('ARG-002')
    expect(ids(section.routes[1].incidents)).toEqual(['r1-b', 'r1-a'])
  })

  it('keeps one route under several tiers when its incidents differ in tier', () => {
    const feed = groupIncidentFeed([
      makeIncident({ id_incident: 'c', tier: 'critical', severity: 'critical' }),
      makeIncident({ id_incident: 'o', tier: 'old' }),
    ])
    expect(feed.map((s) => s.routes[0].id_route)).toEqual(['r-1', 'r-1'])
  })

  it('carries route_ended for an incident whose route was stopped or completed', () => {
    const feed = groupIncidentFeed([makeIncident({ route_ended: true })])
    expect(feed[0].routes[0].route_ended).toBe(true)
  })

  it('is empty for no incidents', () => {
    expect(groupIncidentFeed([])).toEqual([])
  })
})

describe('newestAlert', () => {
  it('is the last alert (alerts are timestamp ascending)', () => {
    const inc = makeIncident({
      alerts: [makeAlert({ id_alert: 'first' }), makeAlert({ id_alert: 'last' })],
    })
    expect(newestAlert(inc)?.id_alert).toBe('last')
  })

  it('is null for an incident with no alerts', () => {
    expect(newestAlert(makeIncident({ alerts: [] }))).toBeNull()
  })
})

describe('roles', () => {
  it('only guardian and root_admin close incidents', () => {
    expect(canCloseIncidents('guardian')).toBe(true)
    expect(canCloseIncidents('root_admin')).toBe(true)
    expect(canCloseIncidents('admin')).toBe(false)
    expect(canCloseIncidents('truck_driver')).toBe(false)
  })

  it('only root_admin and admin complete or stop routes', () => {
    expect(canEndRoutes('root_admin')).toBe(true)
    expect(canEndRoutes('admin')).toBe(true)
    expect(canEndRoutes('guardian')).toBe(false)
    expect(canEndRoutes('truck_driver')).toBe(false)
  })

  it('offers Stop route only before the route has ended', () => {
    expect(canStopRoute({ operative_status: 'scheduled' })).toBe(true)
    expect(canStopRoute({ operative_status: 'in_progress' })).toBe(true)
    expect(canStopRoute({ operative_status: 'in_progress_alert' })).toBe(true)
    expect(canStopRoute({ operative_status: 'completed' })).toBe(false)
    expect(canStopRoute({ operative_status: 'cancelled' })).toBe(false)
  })
})

describe('closeNoteError', () => {
  it('requires a note to close a critical incident', () => {
    expect(closeNoteError('critical', '')).toMatch(/note is required/)
    expect(closeNoteError('critical', '   \n ')).toMatch(/note is required/)
    expect(closeNoteError('critical', 'Called the driver, he pulled over')).toBeNull()
  })

  it('lets a medium incident close without a note', () => {
    expect(closeNoteError('medium', '')).toBeNull()
  })

  it('rejects a note over 1000 characters (after trimming)', () => {
    expect(closeNoteError('medium', 'x'.repeat(1001))).toMatch(/at most 1000/)
    expect(closeNoteError('medium', ` ${'x'.repeat(1000)} `)).toBeNull()
  })
})

describe('stopReasonError', () => {
  it('requires a reason', () => {
    expect(stopReasonError('')).toMatch(/reason is required/)
    expect(stopReasonError('   ')).toMatch(/reason is required/)
    expect(stopReasonError('Truck broke down')).toBeNull()
  })

  it('rejects a reason over 500 characters (after trimming)', () => {
    expect(stopReasonError('x'.repeat(501))).toMatch(/at most 500/)
    expect(stopReasonError('x'.repeat(500))).toBeNull()
  })
})
