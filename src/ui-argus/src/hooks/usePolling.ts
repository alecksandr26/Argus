import { useEffect, useRef } from 'react'

export interface PollingOptions {
  /** `false` stops polling entirely (default `true`). */
  enabled?: boolean
  /** Stop while the browser tab is hidden and poll at once when it shows again (default `true`). */
  pauseWhenHidden?: boolean
  /** Any value whose change restarts polling with an immediate poll (e.g. the route id). */
  resetKey?: unknown
}

/**
 * Calls `poll` at once and then every `intervalMs` — only the data refreshes, never the page.
 *
 * - While the tab is hidden nothing is fetched (`document.visibilityState`); when it becomes
 *   visible again `poll` runs immediately and the interval restarts.
 * - A tick is skipped while the previous `poll` promise is still pending, so a slow network
 *   never stacks requests.
 * - `poll` is read through a ref: passing a new function each render doesn't restart anything.
 *   Use `resetKey` for that.
 */
export function usePolling(
  poll: () => unknown,
  intervalMs: number,
  { enabled = true, pauseWhenHidden = true, resetKey }: PollingOptions = {},
): void {
  const pollRef = useRef(poll)
  pollRef.current = poll

  useEffect(() => {
    if (!enabled) return
    let inFlight = false
    let timer: ReturnType<typeof setInterval> | null = null
    let disposed = false

    const run = () => {
      if (inFlight || disposed) return
      let result: unknown
      try {
        result = pollRef.current()
      } catch {
        return
      }
      if (result instanceof Promise) {
        inFlight = true
        result.catch(() => {}).finally(() => {
          inFlight = false
        })
      }
    }
    const start = () => {
      if (timer !== null) return
      run()
      timer = setInterval(run, intervalMs)
    }
    const stop = () => {
      if (timer !== null) clearInterval(timer)
      timer = null
    }
    const hidden = () => typeof document !== 'undefined' && document.visibilityState === 'hidden'
    const onVisibility = () => {
      if (hidden()) stop()
      else start()
    }

    if (pauseWhenHidden && hidden()) {
      // Start when the tab first shows.
    } else {
      start()
    }
    if (pauseWhenHidden) document.addEventListener('visibilitychange', onVisibility)
    return () => {
      disposed = true
      stop()
      if (pauseWhenHidden) document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [intervalMs, enabled, pauseWhenHidden, resetKey])
}
