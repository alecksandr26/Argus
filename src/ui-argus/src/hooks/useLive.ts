import { useCallback, useEffect, useRef, useState } from 'react'
import { getLive } from '../api/live'
import { ApiError } from '../api/client'
import { UI_POLL_MS } from '../constants'
import { EMPTY_LIVE, mergeLive, type LiveState } from '../utils/live'
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
 * `route` scopes it to one route (the route view); changing it starts over (new scope, no
 * cursor). Responses that arrive for an old scope are dropped.
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

  useEffect(() => {
    scopeRef.current += 1
    stateRef.current = EMPTY_LIVE
    setLive(EMPTY_LIVE)
    setLoading(true)
    setError(null)
    setErrorStatus(null)
  }, [token, route])

  const refresh = useCallback(async () => {
    const scope = scopeRef.current
    try {
      const res = await getLive(token, { since: stateRef.current.serverTime, route })
      if (scope !== scopeRef.current) return
      const next = mergeLive(stateRef.current, res, { route })
      stateRef.current = next
      setLive(next)
      setError(null)
      setErrorStatus(null)
    } catch (err) {
      if (scope !== scopeRef.current) return
      setError(err instanceof ApiError ? err.message : 'Failed to load live data')
      setErrorStatus(err instanceof ApiError ? err.status : null)
    } finally {
      if (scope === scopeRef.current) setLoading(false)
    }
  }, [token, route])

  usePolling(refresh, UI_POLL_MS, { resetKey: `${token}|${route ?? ''}` })

  return { live, loading, error, errorStatus, refresh }
}
