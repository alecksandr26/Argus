import { describe, it, expect } from 'vitest'
import { appendTrack, EMPTY_LIVE, mergeLive } from './live'
import { at, makeIncident, makeLive, makePoint, makeRouteLive } from '../test/fixtures'

const ts = (ps: { t: string }[]) => ps.map((p) => p.t)

describe('appendTrack', () => {
  it('appends new points in t order', () => {
    const out = appendTrack([makePoint({ t: at(0) })], [makePoint({ t: at(1) }), makePoint({ t: at(2) })])
    expect(ts(out)).toEqual([at(0), at(1), at(2)])
  })

  it('de-duplicates by t, including an equal instant written differently', () => {
    const existing = [makePoint({ t: '2026-10-23T12:00:00Z' })]
    const out = appendTrack(existing, [
      makePoint({ t: '2026-10-23T12:00:00+00:00' }),
      makePoint({ t: at(1) }),
      makePoint({ t: at(1) }),
    ])
    expect(out).toHaveLength(2)
  })

  it('sorts late, out-of-order points into place', () => {
    const out = appendTrack(
      [makePoint({ t: at(0) }), makePoint({ t: at(5) })],
      [makePoint({ t: at(3) }), makePoint({ t: at(1) })],
    )
    expect(ts(out)).toEqual([at(0), at(1), at(3), at(5)])
  })

  it('returns the same array when nothing new arrived', () => {
    const existing = [makePoint({ t: at(0) })]
    expect(appendTrack(existing, [])).toBe(existing)
    expect(appendTrack(existing, [makePoint({ t: at(0) })])).toBe(existing)
  })

  it('keeps only the newest max points', () => {
    const out = appendTrack(
      [makePoint({ t: at(0) }), makePoint({ t: at(1) })],
      [makePoint({ t: at(2) }), makePoint({ t: at(3) })],
      3,
    )
    expect(ts(out)).toEqual([at(1), at(2), at(3)])
  })

  it('skips a point with an unparseable time', () => {
    expect(appendTrack([], [makePoint({ t: 'garbage' })])).toEqual([])
  })
})

describe('mergeLive', () => {
  it('stores the cursor, routes and incidents of the response', () => {
    const inc = makeIncident()
    const res = makeLive({ server_time: at(10), incidents: [inc] })
    const s = mergeLive(EMPTY_LIVE, res)
    expect(s.serverTime).toBe(at(10))
    expect(s.routes).toBe(res.routes)
    expect(s.incidents).toEqual([inc])
  })

  it('replaces routes and incidents in full on every poll (no delta)', () => {
    const first = mergeLive(
      EMPTY_LIVE,
      makeLive({ incidents: [makeIncident({ id_incident: 'i-1' }), makeIncident({ id_incident: 'i-2' })] }),
    )
    const second = mergeLive(first, makeLive({ incidents: [makeIncident({ id_incident: 'i-2' })] }))
    expect(second.incidents.map((i) => i.id_incident)).toEqual(['i-2'])
  })

  it('trusts the server tier as given (never recomputes "old")', () => {
    const stale = makeIncident({ tier: 'critical', severity: 'critical', last_alert_at: '2020-01-01T00:00:00Z' })
    const s = mergeLive(EMPTY_LIVE, makeLive({ incidents: [stale] }))
    expect(s.incidents[0].tier).toBe('critical')
  })

  it('appends the track delta per route across polls, de-duplicated', () => {
    const r2 = makeRouteLive({ id_route: 'r-2' })
    const routes = [makeRouteLive(), r2]
    const s1 = mergeLive(
      EMPTY_LIVE,
      makeLive({
        routes,
        track: [
          makePoint({ t: at(0) }),
          makePoint({ t: at(1) }),
          makePoint({ id_route: 'r-2', t: at(0) }),
        ],
      }),
    )
    const s2 = mergeLive(
      s1,
      makeLive({ routes, track: [makePoint({ t: at(1) }), makePoint({ t: at(2) })] }),
    )
    expect(ts(s2.track['r-1'])).toEqual([at(0), at(1), at(2)])
    expect(ts(s2.track['r-2'])).toEqual([at(0)])
    // r-2 got nothing new: its array is kept as-is.
    expect(s2.track['r-2']).toBe(s1.track['r-2'])
  })

  it('without a route filter, drops the track of a route that left the active set', () => {
    const s1 = mergeLive(
      EMPTY_LIVE,
      makeLive({
        routes: [makeRouteLive(), makeRouteLive({ id_route: 'r-2' })],
        track: [makePoint({ t: at(0) }), makePoint({ id_route: 'r-2', t: at(0) })],
      }),
    )
    // r-2 was stopped: it's gone from `routes`, and so is its path.
    const s2 = mergeLive(s1, makeLive({ routes: [makeRouteLive()], track: [] }))
    expect(Object.keys(s2.track)).toEqual(['r-1'])
  })

  it('ignores delta points for a route that is not in the response routes', () => {
    const s = mergeLive(
      EMPTY_LIVE,
      makeLive({ routes: [makeRouteLive()], track: [makePoint({ id_route: 'r-9', t: at(0) })] }),
    )
    expect(s.track['r-9']).toBeUndefined()
  })

  it('with a route filter, keeps only that route (whatever its status)', () => {
    const ended = makeRouteLive({ id_route: 'r-5', operative_status: 'cancelled' })
    const s = mergeLive(
      EMPTY_LIVE,
      makeLive({
        routes: [ended],
        track: [makePoint({ id_route: 'r-5', t: at(0) }), makePoint({ id_route: 'r-1', t: at(0) })],
      }),
      { route: 'r-5' },
    )
    expect(Object.keys(s.track)).toEqual(['r-5'])
  })

  it('caps each route to maxPointsPerRoute', () => {
    const track = [0, 1, 2, 3, 4].map((i) => makePoint({ t: at(i) }))
    const s = mergeLive(EMPTY_LIVE, makeLive({ track }), { maxPointsPerRoute: 2 })
    expect(ts(s.track['r-1'])).toEqual([at(3), at(4)])
  })
})
