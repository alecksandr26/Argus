import { describe, it, expect } from 'vitest'
import { alertPins, incidentPins, routeMarker, routeMarkers, trackPath, trackPaths } from './mapLayers'
import { at, makeAlert, makeIncident, makePoint, makeRouteLive } from '../test/fixtures'

const pos = (over = {}) => ({
  t: at(0),
  lat: 20.6597,
  lon: -103.3496,
  speed: 61,
  vigilance: 'low' as const,
  last_known: false,
  location_age_s: 0,
  ...over,
})

describe('routeMarker', () => {
  it('is keyed by id_route and coloured by incident_state, not by the point vigilance', () => {
    const m = routeMarker(
      makeRouteLive({
        id_route: 'r-9',
        incident_state: 'critical',
        latest_position: pos({ vigilance: 'low' }),
      }),
    )!
    expect(m.id).toBe('r-9')
    expect(m.tone).toBe('bad')
    expect(m.stateLabel).toBe('Critical')
    expect(m.vigilanceLabel).toBe('Normal')
    expect(m.position).toEqual([20.6597, -103.3496])
    expect(m.speedKmh).toBe(61)
    expect(m.lastKnown).toBe(false)
    expect(m.positionLabel).toBeNull()
  })

  it('a fine route with a critical point stays green (points never raise the state)', () => {
    const m = routeMarker(
      makeRouteLive({ incident_state: 'fine', latest_position: pos({ vigilance: 'critical' }) }),
    )!
    expect(m.tone).toBe('good')
    expect(m.vigilanceLabel).toBe('Critical')
  })

  it('a last-known position gets the stale style and its age label, and no speed', () => {
    const m = routeMarker(
      makeRouteLive({
        incident_state: 'medium',
        latest_position: pos({ last_known: true, location_age_s: 180, speed: 0 }),
      }),
    )!
    expect(m.tone).toBe('warn')
    expect(m.lastKnown).toBe(true)
    expect(m.positionLabel).toBe('Last known (3 min old)')
    expect(m.speedKmh).toBeNull()
  })

  it('a route without a position has no marker', () => {
    expect(routeMarker(makeRouteLive({ latest_position: null }))).toBeNull()
    expect(
      routeMarkers([
        makeRouteLive({ id_route: 'a', latest_position: null }),
        makeRouteLive({ id_route: 'b', latest_position: pos() }),
      ]).map((m) => m.id),
    ).toEqual(['b'])
  })
})

describe('trackPath', () => {
  it('draws the live points in order and leaves last-known points out', () => {
    const path = trackPath('r-1', [
      makePoint({ t: at(0), lat: 20, lon: -103 }),
      makePoint({ t: at(1), lat: 20.1, lon: -103.1, last_known: true }),
      makePoint({ t: at(2), lat: 20.2, lon: -103.2 }),
    ])!
    expect(path.id).toBe('r-1')
    expect(path.positions).toEqual([
      [20, -103],
      [20.2, -103.2],
    ])
  })

  it('needs at least two live points', () => {
    expect(trackPath('r-1', [makePoint()])).toBeNull()
    expect(trackPath('r-1', [])).toBeNull()
  })

  it('one path per route', () => {
    const paths = trackPaths({
      'r-1': [makePoint({ t: at(0) }), makePoint({ t: at(1) })],
      'r-2': [makePoint({ id_route: 'r-2' })],
    })
    expect(paths.map((p) => p.id)).toEqual(['r-1'])
  })
})

describe('alert pins', () => {
  it('one pin per alert with a position, coloured by severity, with its labels', () => {
    const pins = incidentPins([
      makeIncident({
        alerts: [
          makeAlert({ id_alert: 'a-1', severity_level: 'medium', alert_type: 'eyes_closed' }),
          makeAlert({
            id_alert: 'a-2',
            severity_level: 'critical',
            alert_type: 'no_face',
            delivery: 'dlq',
            location_is_last_known: true,
            location_age_s: 45,
          }),
          makeAlert({ id_alert: 'a-3', coordinates: null, position_unknown: true }),
        ],
      }),
    ])
    expect(pins.map((p) => p.id)).toEqual(['a-1', 'a-2'])
    expect(pins[0]).toMatchObject({ tone: 'warn', title: 'Eyes closed · Medium', notes: [] })
    expect(pins[1]).toMatchObject({
      tone: 'bad',
      title: 'Driver not visible · Critical',
      notes: ['Last known (45s old)', 'Recovered after lost connection'],
    })
  })

  it('de-duplicates by alert id', () => {
    expect(alertPins([makeAlert(), makeAlert()])).toHaveLength(1)
  })
})
