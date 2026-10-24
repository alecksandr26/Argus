/**
 * How a position is labelled everywhere it's shown (contract C3.7): an alert card, alert/route
 * history rows, map markers. One function so every screen says the same words.
 *
 * - no position at all -> "Position unknown"
 * - the device's saved last good fix -> "Last known (N old)", N from `location_age_s`
 * - a live fix -> no label
 */

import { coords, durationShort } from './format'
import type { Alert, Coordinates, LivePosition } from '../types'

export type PositionKind = 'live' | 'last_known' | 'unknown'

export interface PositionInfo {
  kind: PositionKind
  /** `null` iff `kind === 'unknown'`. */
  coordinates: Coordinates | null
  /** Seconds old the fix was, for `last_known` (`null` if the device didn't say). */
  ageSeconds: number | null
  /** "Position unknown" / "Last known (3 min old)"; `null` for a live fix. */
  label: string | null
}

export const POSITION_UNKNOWN_LABEL = 'Position unknown'

/** "Last known (3 min old)", or "Last known (age unknown)" without an age. */
export function lastKnownLabel(ageSeconds: number | null | undefined): string {
  return ageSeconds == null || !Number.isFinite(ageSeconds)
    ? 'Last known (age unknown)'
    : `Last known (${durationShort(ageSeconds)} old)`
}

function info(
  coordinates: Coordinates | null,
  lastKnown: boolean,
  ageSeconds: number | null,
): PositionInfo {
  if (coordinates == null) {
    return { kind: 'unknown', coordinates: null, ageSeconds: null, label: POSITION_UNKNOWN_LABEL }
  }
  if (lastKnown) {
    return { kind: 'last_known', coordinates, ageSeconds, label: lastKnownLabel(ageSeconds) }
  }
  return { kind: 'live', coordinates, ageSeconds: null, label: null }
}

/** An alert's position. `position_unknown` wins even if stray coordinates came along. */
export function alertPosition(
  a: Pick<
    Alert,
    'coordinates' | 'position_unknown' | 'location_is_last_known' | 'location_age_s'
  >,
): PositionInfo {
  const c = a.position_unknown ? null : a.coordinates
  return info(c, a.location_is_last_known, a.location_age_s)
}

/** A GPS point's position (`/api/live` `latest_position`, track points). */
export function pointPosition(p: LivePosition): PositionInfo {
  return info({ lat: p.lat, lon: p.lon }, p.last_known, p.location_age_s)
}

/** One line of text: "20.6597, -103.3496", "20.6597, -103.3496 · Last known (3 min old)" or
 * "Position unknown". */
export function positionSummary(p: PositionInfo): string {
  if (!p.coordinates) return p.label ?? POSITION_UNKNOWN_LABEL
  return p.label ? `${coords(p.coordinates)} · ${p.label}` : coords(p.coordinates)
}
