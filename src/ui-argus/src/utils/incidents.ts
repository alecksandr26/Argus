/**
 * Incident feed logic (contract C3.4/C3.6) and the client-side checks of the close/stop
 * dialogs. Pure functions; the screens only render what these return.
 *
 * Feed: tier sections in the order Critical → Medium → Old, newest `last_alert_at` first.
 * Inside a section, incidents of the same route sit under one route header, routes ordered by
 * their newest incident. The `tier` is the server's (computed at response time); the UI only
 * re-sorts, it never recomputes "old" between polls (`[sync 1]`).
 */

import type { Incident, IncidentTier, Role, Route, RouteStatus } from '../types'

export const TIER_ORDER: readonly IncidentTier[] = ['critical', 'medium', 'old']

/** Max lengths the backend enforces (C4: `CLOSE_NOTE_MAX_LEN`, `STOP_REASON_MAX_LEN`). */
export const CLOSE_NOTE_MAX_LEN = 1000
export const STOP_REASON_MAX_LEN = 500

const ms = (iso: string) => Date.parse(iso)

/** Feed order: tier, then `last_alert_at` descending (ties: id, for a stable order). */
export function compareIncidents(a: Incident, b: Incident): number {
  const tier = TIER_ORDER.indexOf(a.tier) - TIER_ORDER.indexOf(b.tier)
  if (tier !== 0) return tier
  const last = ms(b.last_alert_at) - ms(a.last_alert_at)
  if (last !== 0) return last
  return a.id_incident < b.id_incident ? -1 : a.id_incident > b.id_incident ? 1 : 0
}

export function sortIncidents(incidents: readonly Incident[]): Incident[] {
  return [...incidents].sort(compareIncidents)
}

export interface IncidentRouteGroup {
  id_route: string
  truck_plate_number: string | null
  driver_full_name: string | null
  route_ended: boolean
  /** Newest first. */
  incidents: Incident[]
}

export interface IncidentTierSection {
  tier: IncidentTier
  /** Ordered by each route's newest incident. */
  routes: IncidentRouteGroup[]
  /** Total incidents in this section. */
  count: number
}

/** Builds the tiered, route-grouped feed. Empty tiers are left out. */
export function groupIncidentFeed(incidents: readonly Incident[]): IncidentTierSection[] {
  const sorted = sortIncidents(incidents)
  const sections: IncidentTierSection[] = []
  for (const tier of TIER_ORDER) {
    const inTier = sorted.filter((i) => i.tier === tier)
    if (inTier.length === 0) continue
    // `inTier` is newest first, so the first incident seen per route is its newest: inserting
    // routes in first-seen order orders them by their newest incident.
    const byRoute = new Map<string, IncidentRouteGroup>()
    for (const inc of inTier) {
      let group = byRoute.get(inc.id_route)
      if (!group) {
        group = {
          id_route: inc.id_route,
          truck_plate_number: inc.truck_plate_number,
          driver_full_name: inc.driver_full_name,
          route_ended: inc.route_ended,
          incidents: [],
        }
        byRoute.set(inc.id_route, group)
      }
      group.incidents.push(inc)
    }
    sections.push({ tier, routes: [...byRoute.values()], count: inTier.length })
  }
  return sections
}

/** The newest alert of an incident (its alerts are timestamp ascending), or `null`. */
export function newestAlert(incident: Incident) {
  return incident.alerts.length ? incident.alerts[incident.alerts.length - 1] : null
}

// --- roles and dialog checks ----------------------------------------------------------------

/** Who may close incidents (C3.6): guardian + root_admin. */
export function canCloseIncidents(role: Role): boolean {
  return role === 'guardian' || role === 'root_admin'
}

/** Who may complete or stop routes: root_admin + admin. */
export function canEndRoutes(role: Role): boolean {
  return role === 'root_admin' || role === 'admin'
}

const STOPPABLE: readonly RouteStatus[] = ['scheduled', 'in_progress', 'in_progress_alert']

/** "Stop route" is offered for a route that hasn't ended yet (the backend 409s otherwise). */
export function canStopRoute(route: Pick<Route, 'operative_status'>): boolean {
  return STOPPABLE.includes(route.operative_status)
}

/** Validation message for the close dialog's note, or `null` when it may be sent. A critical
 * incident needs a non-empty note; a medium one may be closed without. */
export function closeNoteError(
  severity: Incident['severity'],
  note: string,
): string | null {
  const trimmed = note.trim()
  if (severity === 'critical' && trimmed === '') {
    return 'A note is required to close a critical incident'
  }
  if (trimmed.length > CLOSE_NOTE_MAX_LEN) {
    return `The note can be at most ${CLOSE_NOTE_MAX_LEN} characters`
  }
  return null
}

/** Validation message for the stop dialog's reason, or `null` when it may be sent. */
export function stopReasonError(reason: string): string | null {
  const trimmed = reason.trim()
  if (trimmed === '') return 'A reason is required to stop a route'
  if (trimmed.length > STOP_REASON_MAX_LEN) {
    return `The reason can be at most ${STOP_REASON_MAX_LEN} characters`
  }
  return null
}
