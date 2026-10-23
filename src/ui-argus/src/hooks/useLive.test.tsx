import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useLive } from './useLive'
import { UI_POLL_MS } from '../constants'
import { at, makeIncident, makeLive, makePoint, makeRouteLive } from '../test/fixtures'

function ok(body: unknown) {
  return { ok: true, status: 200, json: async () => body }
}

const url = (fetchMock: ReturnType<typeof vi.fn>, i: number) => new URL(fetchMock.mock.calls[i][0])

/** Only the `/api/live` calls (the route view also fetches `/api/routes/{id}/track`). */
const liveUrls = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.map(([u]) => new URL(u)).filter((u) => u.pathname === '/api/live')

function deferred<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

beforeEach(() => {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('useLive', () => {
  it('polls /api/live every UI_POLL_MS, passing the previous server_time as since', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        ok(makeLive({ server_time: at(5), track: [makePoint({ t: at(0) }), makePoint({ t: at(1) })] })),
      )
      .mockResolvedValueOnce(
        ok(
          makeLive({
            server_time: at(10),
            incidents: [makeIncident()],
            track: [makePoint({ t: at(1) }), makePoint({ t: at(6) })],
          }),
        ),
      )
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useLive('tok'))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(url(fetchMock, 0).searchParams.get('since')).toBeNull()
    expect(result.current.live.serverTime).toBe(at(5))
    expect(result.current.live.track['r-1']).toHaveLength(2)

    await act(async () => {
      vi.advanceTimersByTime(UI_POLL_MS)
    })
    await waitFor(() => expect(result.current.live.serverTime).toBe(at(10)))
    expect(url(fetchMock, 1).searchParams.get('since')).toBe(at(5))
    expect(result.current.live.track['r-1'].map((p) => p.t)).toEqual([at(0), at(1), at(6)])
    expect(result.current.live.incidents).toHaveLength(1)
  })

  it('scopes to one route and starts over (no cursor) when the route changes', async () => {
    const fetchMock = vi.fn(async (u: string) => {
      const parsed = new URL(u)
      if (parsed.pathname.endsWith('/track')) {
        return ok({ id_route: parsed.pathname.split('/')[3], downsampled: false, points: [] })
      }
      const route = parsed.searchParams.get('route')!
      return ok(
        makeLive({
          server_time: at(5),
          routes: [makeRouteLive({ id_route: route })],
          track: [makePoint({ id_route: route, t: at(0) })],
        }),
      )
    })
    vi.stubGlobal('fetch', fetchMock)

    const { result, rerender } = renderHook(({ route }) => useLive('tok', route), {
      initialProps: { route: 'r-1' },
    })
    await waitFor(() => expect(result.current.live.track['r-1']).toHaveLength(1))
    expect(liveUrls(fetchMock)[0].searchParams.get('route')).toBe('r-1')

    rerender({ route: 'r-2' })
    await waitFor(() => expect(result.current.live.track['r-2']).toHaveLength(1))
    const lives = liveUrls(fetchMock)
    const last = lives[lives.length - 1]
    expect(last.searchParams.get('route')).toBe('r-2')
    expect(last.searchParams.get('since')).toBeNull()
    expect(result.current.live.track['r-1']).toBeUndefined()
  })

  it('reports an error with its status, and keeps the last good data', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(ok(makeLive({ incidents: [makeIncident()] })))
      .mockResolvedValueOnce({ ok: false, status: 503, json: async () => ({ detail: 'Down' }) })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useLive('tok'))
    await waitFor(() => expect(result.current.live.incidents).toHaveLength(1))
    await act(async () => {
      vi.advanceTimersByTime(UI_POLL_MS)
    })
    await waitFor(() => expect(result.current.error).toBe('Down'))
    expect(result.current.errorStatus).toBe(503)
    expect(result.current.live.incidents).toHaveLength(1)
  })

  it('refresh() polls right away', async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok(makeLive()))
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useLive('tok'))
    await waitFor(() => expect(result.current.loading).toBe(false))
    const before = fetchMock.mock.calls.length
    await act(async () => {
      await result.current.refresh()
    })
    expect(fetchMock.mock.calls.length).toBe(before + 1)
  })

  it('applies responses in request order: an older poll landing last is dropped', async () => {
    const first = deferred<ReturnType<typeof ok>>()
    const second = deferred<ReturnType<typeof ok>>()
    const fetchMock = vi
      .fn()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useLive('tok'))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))

    // A refresh (e.g. right after closing the incident) overlaps the in-flight interval poll.
    let refreshed!: Promise<void>
    act(() => {
      refreshed = result.current.refresh()
    })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    // The newer request answers first: the incident is closed.
    await act(async () => {
      second.resolve(ok(makeLive({ server_time: at(10), incidents: [] })))
      await refreshed
    })
    expect(result.current.live.serverTime).toBe(at(10))

    // The older poll lands last with the incident still open: it must not be applied.
    await act(async () => {
      first.resolve(ok(makeLive({ server_time: at(5), incidents: [makeIncident()] })))
      await first.promise
    })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.live.incidents).toEqual([])
    expect(result.current.live.serverTime).toBe(at(10))
  })

  it('route view: seeds the whole path from /track, then folds the live deltas in', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const track = deferred<ReturnType<typeof ok>>()
    let livePolls = 0
    const fetchMock = vi.fn(async (u: string) => {
      const parsed = new URL(u)
      if (parsed.pathname === '/api/routes/r-1/track') return track.promise
      livePolls += 1
      return ok(
        livePolls === 1
          ? makeLive({ server_time: at(3600), track: [makePoint({ t: at(3590) })] })
          : makeLive({
              server_time: at(3605),
              track: [makePoint({ t: at(3590) }), makePoint({ t: at(3601) })],
            }),
      )
    })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useLive('tok', 'r-1'))
    // The live poll (last 15 min only) lands before the seed.
    await waitFor(() => expect(result.current.live.track['r-1']).toHaveLength(1))

    // The /track seed carries the start of the route (and overlaps the live point).
    await act(async () => {
      track.resolve(
        ok({
          id_route: 'r-1',
          downsampled: false,
          points: [makePoint({ t: at(0) }), makePoint({ t: at(60) }), makePoint({ t: at(3590) })],
        }),
      )
      await track.promise
    })
    await waitFor(() =>
      expect(result.current.live.track['r-1'].map((p) => p.t)).toEqual([at(0), at(60), at(3590)]),
    )

    await act(async () => {
      vi.advanceTimersByTime(UI_POLL_MS)
    })
    await waitFor(() =>
      expect(result.current.live.track['r-1'].map((p) => p.t)).toEqual([
        at(0),
        at(60),
        at(3590),
        at(3601),
      ]),
    )
    // Seeded once, not on every poll.
    expect(fetchMock.mock.calls.filter(([u]) => String(u).includes('/track'))).toHaveLength(1)
  })

  it('route view: a failed seed is retried on the next poll', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let trackCalls = 0
    const fetchMock = vi.fn(async (u: string) => {
      if (String(u).includes('/track')) {
        trackCalls += 1
        if (trackCalls === 1) return { ok: false, status: 503, json: async () => ({ detail: 'Down' }) }
        return ok({ id_route: 'r-1', downsampled: true, points: [makePoint({ t: at(0) })] })
      }
      return ok(makeLive({ track: [] }))
    })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useLive('tok', 'r-1'))
    await waitFor(() => expect(trackCalls).toBe(1))
    await act(async () => {
      vi.advanceTimersByTime(UI_POLL_MS)
    })
    await waitFor(() => expect(result.current.live.track['r-1']).toHaveLength(1))
    expect(trackCalls).toBe(2)
  })
})
