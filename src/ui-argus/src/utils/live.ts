/**
 * Client-side state for `GET /api/live` (contract C3.6): how successive poll responses are
 * merged. Pure functions, so the merge rules are unit-tested without React.
 *
 * Merge rules (C3.6 + `[sync 1]`):
 * - `routes` and `incidents` come in full on every response and simply replace the previous
 *   ones. The server's incident `tier` is trusted as-is (never recomputed between polls).
 * - `track` is a delta: points received since the `since` cursor. They are appended and
 *   de-duplicated by `(id_route, t)`, kept `t`-ascending per route.
 * - Without a `route` filter (Live operations), after each merge only the track of routes still
 *   in the latest `routes` array is kept — a stopped/completed route's path disappears.
 *   With `route=<id>` (the route view), only that route's points are kept.
 * - Each route keeps at most `LIVE_TRACK_MAX_POINTS_PER_ROUTE` points (newest kept).
 */

import { LIVE_TRACK_MAX_POINTS_PER_ROUTE } from '../constants'
import type { Incident, LiveResponse, RouteLive, TrackPoint } from '../types'

export interface LiveState {
  /** The last response's `server_time` — the `since` for the next poll; `null` before the first. */
  serverTime: string | null
  routes: RouteLive[]
  incidents: Incident[]
  /** Points per route id, `t` ascending, de-duplicated. */
  track: Record<string, TrackPoint[]>
}

export const EMPTY_LIVE: LiveState = { serverTime: null, routes: [], incidents: [], track: {} }

export interface MergeOptions {
  /** The `route` filter the response was fetched with (the route view), if any. */
  route?: string | null
  maxPointsPerRoute?: number
}

const tMs = (p: TrackPoint) => Date.parse(p.t)

/** Appends `incoming` to `existing` (both for one route), de-duplicating by `t` and keeping the
 * result `t`-ascending and capped to the newest `max` points. Returns `existing` itself when
 * nothing new arrived, so React consumers can skip re-rendering that route's path. */
export function appendTrack(
  existing: TrackPoint[],
  incoming: TrackPoint[],
  max: number = LIVE_TRACK_MAX_POINTS_PER_ROUTE,
): TrackPoint[] {
  if (incoming.length === 0) return existing
  const seen = new Set(existing.map(tMs))
  const added: TrackPoint[] = []
  for (const p of incoming) {
    const k = tMs(p)
    if (Number.isNaN(k) || seen.has(k)) continue
    seen.add(k)
    added.push(p)
  }
  if (added.length === 0) return existing
  let merged = existing.concat(added)
  // The common case is "all new points are newer than the newest kept one": skip the sort then.
  const lastExisting = existing.length ? tMs(existing[existing.length - 1]) : -Infinity
  if (added.some((p) => tMs(p) < lastExisting) || !isAscending(added)) {
    merged = merged.sort((a, b) => tMs(a) - tMs(b))
  }
  return merged.length > max ? merged.slice(merged.length - max) : merged
}

function isAscending(ps: TrackPoint[]): boolean {
  for (let i = 1; i < ps.length; i++) if (tMs(ps[i]) < tMs(ps[i - 1])) return false
  return true
}

/** Folds one `/api/live` response into the previous state (see the module comment). */
export function mergeLive(
  prev: LiveState,
  res: LiveResponse,
  opts: MergeOptions = {},
): LiveState {
  const max = opts.maxPointsPerRoute ?? LIVE_TRACK_MAX_POINTS_PER_ROUTE
  const keep = opts.route
    ? new Set([opts.route])
    : new Set(res.routes.map((r) => r.id_route))

  const incomingByRoute = new Map<string, TrackPoint[]>()
  for (const p of res.track) {
    if (!keep.has(p.id_route)) continue
    const list = incomingByRoute.get(p.id_route)
    if (list) list.push(p)
    else incomingByRoute.set(p.id_route, [p])
  }

  const track: Record<string, TrackPoint[]> = {}
  for (const id of keep) {
    const merged = appendTrack(prev.track[id] ?? [], incomingByRoute.get(id) ?? [], max)
    if (merged.length) track[id] = merged
  }

  return {
    serverTime: res.server_time,
    routes: res.routes,
    incidents: res.incidents,
    track,
  }
}
