import { useCallback, useEffect, useRef, useState } from 'react'
import { getLive } from '../api/live'
import { getRouteTrack } from '../api/routes'
import { ApiError } from '../api/client'
import { UI_POLL_MS } from '../constants'
import { EMPTY_LIVE, appendTrack, mergeLive, type LiveState } from '../utils/live'
import { usePolling } from './usePolling'

export interface UseLiveResult {
  live: LiveState
  /** `true` until the first response (or error) for the current scope. */
  loading: boolean
  /** The last poll's error message, cleared by the next good poll. */
  error: string | null
  /** HTTP status of that error, if it was an `ApiError` (e.g. 404 for an unknown route). */
  errorStatus: number | null
  /** Poll right now (e.g. after closing an incident), outside the interval. */
  refresh: () => Promise<void>
}

/**
 * `GET /api/live` every `UI_POLL_MS`, paused while the tab is hidden, merged with `mergeLive()`.
 *
 * - `route` scopes it to one route (the route view); changing it starts over (new scope, no
 *   cursor). Responses that arrive for an old scope are dropped.
 * - Responses are applied in the order the requests were **sent**, not the order they arrive:
 *   each request takes a sequence number, and a response older than the last one applied is
 *   dropped. `refresh()` (e.g. right after closing an incident) can run while an interval poll
 *   is still in flight; without this, that older poll could land last and bring the closed
 *   incident back for one interval.
 * - With a `route`, the whole path is seeded once from `GET /api/routes/{id}/track` (any route,
 *   active or ended, C3.6 `[sync 2]`): `/api/live` without `since` only returns the last 15 min.
 *   The seed and the live deltas fold together with `appendTrack` (both de-duplicate by `t`), in
 *   whichever order they arrive. A failed seed is retried on the next poll.
 */
export function useLive(token: string, route: string | null = null): UseLiveResult {
  const [live, setLive] = useState<LiveState>(EMPTY_LIVE)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [errorStatus, setErrorStatus] = useState<number | null>(null)

  // The cursor and the merged state live in refs too, so a poll always builds on the latest
  // response even if React hasn't re-rendered between two of them.
  const stateRef = useRef<LiveState>(EMPTY_LIVE)
  const scopeRef = useRef(0)
  /** Sequence number of the last request started, and of the last response applied. */
  const sentSeqRef = useRef(0)
  const appliedSeqRef = useRef(0)
  /** The route view's track seed: `idle` (not fetched yet, or failed), `pending`, `done`. */
  const seedRef = useRef<'idle' | 'pending' | 'done'>('idle')

  useEffect(() => {
    scopeRef.current += 1
    stateRef.current = EMPTY_LIVE
    appliedSeqRef.current = sentSeqRef.current
    seedRef.current = 'idle'
    setLive(EMPTY_LIVE)
    setLoading(true)
    setError(null)
    setErrorStatus(null)
  }, [token, route])

  const seedTrack = useCallback(
    (scope: number, routeId: string) => {
      seedRef.current = 'pending'
      getRouteTrack(routeId, token)
        .then((res) => {
          if (scope !== scopeRef.current) return
          seedRef.current = 'done'
          const prev = stateRef.current
          const existing = prev.track[routeId] ?? []
          const merged = appendTrack(existing, res.points)
          if (merged === existing) return
          const next: LiveState = { ...prev, track: { ...prev.track, [routeId]: merged } }
          stateRef.current = next
          setLive(next)
        })
        .catch(() => {
          // The live deltas still draw the last 15 min; the seed is retried on the next poll.
          if (scope === scopeRef.current) seedRef.current = 'idle'
        })
    },
    [token],
  )

  const refresh = useCallback(async () => {
    const scope = scopeRef.current
    const seq = ++sentSeqRef.current
    if (route && seedRef.current === 'idle') seedTrack(scope, route)
    try {
      const res = await getLive(token, { since: stateRef.current.serverTime, route })
      if (scope !== scopeRef.current || seq < appliedSeqRef.current) return
      appliedSeqRef.current = seq
      const next = mergeLive(stateRef.current, res, { route })
      stateRef.current = next
      setLive(next)
      setError(null)
      setErrorStatus(null)
    } catch (err) {
      if (scope !== scopeRef.current || seq < appliedSeqRef.current) return
      setError(err instanceof ApiError ? err.message : 'Failed to load live data')
      setErrorStatus(err instanceof ApiError ? err.status : null)
    } finally {
      if (scope === scopeRef.current) setLoading(false)
    }
  }, [token, route, seedTrack])

  usePolling(refresh, UI_POLL_MS, { resetKey: `${token}|${route ?? ''}` })

  return { live, loading, error, errorStatus, refresh }
}
