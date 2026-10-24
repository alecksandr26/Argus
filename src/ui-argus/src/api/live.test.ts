import { describe, it, expect, vi, afterEach } from 'vitest'
import { getLive } from './live'
import { closeIncident, getIncident, listIncidents } from './incidents'
import { getRouteTrack, stopRoute } from './routes'
import { listAlerts } from './alerts'
import { makeAlert, makeIncident, makeLive, makePoint, makeRoute, makeRouteLive } from '../test/fixtures'

function ok(body: unknown) {
  return { ok: true, status: 200, json: async () => body }
}

function mockFetch(body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(ok(body))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('getLive', () => {
  it('GETs /api/live with no query on the first poll', async () => {
    const fetchMock = mockFetch(makeLive())
    await getLive('tok')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toMatch(/\/api\/live$/)
    expect(init.method).toBe('GET')
    expect(init.headers.Authorization).toBe('Bearer tok')
  })

  it('passes since and route', async () => {
    const fetchMock = mockFetch(makeLive())
    await getLive('tok', { since: '2026-10-23T12:00:05+00:00', route: 'r-1' })
    const url = new URL(fetchMock.mock.calls[0][0])
    expect(url.pathname).toBe('/api/live')
    expect(url.searchParams.get('since')).toBe('2026-10-23T12:00:05+00:00')
    expect(url.searchParams.get('route')).toBe('r-1')
  })

  it('normalizes coordinates of routes and of the alerts inside incidents', async () => {
    mockFetch(
      makeLive({
        routes: [makeRouteLive({ destination_coordinates: [-100.39, 20.59] as never })],
        incidents: [
          makeIncident({
            alerts: [
              makeAlert({ coordinates: { type: 'Point', coordinates: [-103.35, 20.66] } as never }),
              makeAlert({ id_alert: 'a-2', coordinates: null, position_unknown: true }),
            ],
          }),
        ],
        track: [makePoint()],
      }),
    )
    const res = await getLive('tok')
    expect(res.routes[0].destination_coordinates).toEqual({ lat: 20.59, lon: -100.39 })
    expect(res.incidents[0].alerts[0].coordinates).toEqual({ lat: 20.66, lon: -103.35 })
    expect(res.incidents[0].alerts[1].coordinates).toBeNull()
    expect(res.track).toHaveLength(1)
  })
})

describe('incidents API', () => {
  it('lists incidents with route/status/limit filters', async () => {
    const fetchMock = mockFetch([makeIncident()])
    const out = await listIncidents('tok', { route_id: 'r-1', status: 'closed', limit: 50 })
    const url = new URL(fetchMock.mock.calls[0][0])
    expect(url.pathname).toBe('/api/incidents')
    expect(url.searchParams.get('route_id')).toBe('r-1')
    expect(url.searchParams.get('status')).toBe('closed')
    expect(url.searchParams.get('limit')).toBe('50')
    expect(out).toHaveLength(1)
  })

  it('gets one incident', async () => {
    const fetchMock = mockFetch(makeIncident())
    await getIncident('i-1', 'tok')
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/incidents\/i-1$/)
  })

  it('closes an incident with its note', async () => {
    const fetchMock = mockFetch(makeIncident({ closed_at: '2026-10-23T13:00:00Z', close_note: 'ok' }))
    const out = await closeIncident('i-1', 'Called the driver', 'tok')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toMatch(/\/api\/incidents\/i-1\/close$/)
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ note: 'Called the driver' })
    expect(out.closed_at).toBe('2026-10-23T13:00:00Z')
  })

  it('surfaces the 422 for a critical incident without a note', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: async () => ({ detail: 'A note is required to close a critical incident' }),
      }),
    )
    await expect(closeIncident('i-1', '', 'tok')).rejects.toMatchObject({
      status: 422,
      message: 'A note is required to close a critical incident',
    })
  })
})

describe('routes API: stop and track', () => {
  it('stops a route with its reason', async () => {
    const fetchMock = mockFetch(
      makeRoute({ operative_status: 'cancelled', stop_reason: 'Breakdown', stopped_by: 'u1' }),
    )
    const out = await stopRoute('r-1', 'Breakdown', 'tok')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toMatch(/\/api\/routes\/r-1\/stop$/)
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ reason: 'Breakdown' })
    expect(out.operative_status).toBe('cancelled')
  })

  it('fetches a route track, with an optional max_points', async () => {
    const fetchMock = mockFetch({ id_route: 'r-1', downsampled: false, points: [makePoint()] })
    const out = await getRouteTrack('r-1', 'tok', 500)
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/routes\/r-1\/track\?max_points=500$/)
    expect(out.points).toHaveLength(1)
    await getRouteTrack('r-1', 'tok')
    expect(fetchMock.mock.calls[1][0]).toMatch(/\/api\/routes\/r-1\/track$/)
  })
})

describe('alerts API', () => {
  it('filters by incident_id and keeps a null position as null', async () => {
    const fetchMock = mockFetch([makeAlert({ coordinates: null, position_unknown: true })])
    const out = await listAlerts('tok', { incident_id: 'i-1' })
    expect(new URL(fetchMock.mock.calls[0][0]).searchParams.get('incident_id')).toBe('i-1')
    expect(out[0].coordinates).toBeNull()
  })
})
