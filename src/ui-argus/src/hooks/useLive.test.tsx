import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useLive } from './useLive'
import { UI_POLL_MS } from '../constants'
import { at, makeIncident, makeLive, makePoint, makeRouteLive } from '../test/fixtures'

function ok(body: unknown) {
  return { ok: true, status: 200, json: async () => body }
}

const url = (fetchMock: ReturnType<typeof vi.fn>, i: number) => new URL(fetchMock.mock.calls[i][0])

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
      const route = new URL(u).searchParams.get('route')!
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
    expect(url(fetchMock, 0).searchParams.get('route')).toBe('r-1')

    rerender({ route: 'r-2' })
    await waitFor(() => expect(result.current.live.track['r-2']).toHaveLength(1))
    const last = url(fetchMock, fetchMock.mock.calls.length - 1)
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
})
