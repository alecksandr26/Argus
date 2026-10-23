/**
 * Maps the domain status unions (see `src/types.ts`) to an English label and a
 * visual "tone" the `<StatusPill>` / stat tiles render. Kept in one place so the
 * colour language stays consistent with the mockups across every screen.
 */

import type {
  AlertDelivery,
  AlertSeverity,
  DriverStatus,
  IncidentState,
  IncidentTier,
  Role,
  Route,
  RouteStatus,
  TruckStatus,
  Vigilance,
} from '../types'

export type Tone = 'good' | 'warn' | 'bad' | 'neutral'

export interface Badge {
  label: string
  tone: Tone
}

export const truckStatus: Record<TruckStatus, Badge> = {
  active: { label: 'Active', tone: 'good' },
  alert: { label: 'Active alert', tone: 'bad' },
  maintenance: { label: 'In shop', tone: 'neutral' },
  inactive: { label: 'Inactive', tone: 'neutral' },
}

export const driverStatus: Record<DriverStatus, Badge> = {
  on_route: { label: 'On route', tone: 'good' },
  on_route_alert: { label: 'On route · alert', tone: 'bad' },
  resting: { label: 'Resting', tone: 'neutral' },
  inactive: { label: 'Inactive', tone: 'neutral' },
}

export const routeStatus: Record<RouteStatus, Badge> = {
  in_progress: { label: 'In progress', tone: 'good' },
  in_progress_alert: { label: 'In progress · alert', tone: 'bad' },
  scheduled: { label: 'Scheduled', tone: 'neutral' },
  completed: { label: 'Completed', tone: 'neutral' },
  cancelled: { label: 'Cancelled', tone: 'warn' },
}

/** Shared by `StatusRoute.vigilance` and `Alert.severity_level` — one severity scale, one label
 * map, instead of the two separate (and previously mismatched) maps this used to be.
 *
 * `low` is labeled "Normal", not "Low" — for a truck's live vigilance, `low` means the driver
 * is fine/no incident open (the reassuring, common case), so a severity-sounding word there
 * reads as alarming rather than as the all-clear it actually is. Alerts, in practice, are never
 * actually posted at `low` severity (the fusion contract — `src/cv-argus/src/orchestrator/
 * fusion_contract.py` — never raises an incident for it), so this relabel has no real
 * downside on the Alert side either; see `LiveOps.tsx`/`RouteLiveView.tsx`/`AlertHistory.tsx`'s
 * severity filter chips, which drop a "Low" option entirely for the same reason. Tone is
 * `good` (green), not `neutral` (gray) — this also fixes a real inconsistency: the map legend
 * and the "Low severity" stat tile were already colored green for this state, but the truck
 * marker dot itself rendered gray/neutral; now all three agree. */
export const severity: Record<AlertSeverity, Badge> = {
  critical: { label: 'Critical', tone: 'bad' },
  medium: { label: 'Medium', tone: 'warn' },
  low: { label: 'Normal', tone: 'good' },
}

/** A GPS point's vigilance (C3.3). `unknown` (no recent Pi reading) is neutral gray: it is
 * neither reassuring nor an alarm. */
export const vigilance: Record<Vigilance, Badge> = {
  ...severity,
  unknown: { label: 'Unknown', tone: 'neutral' },
}

/** A route's `incident_state` (C3.1) — the map marker colour. */
export const incidentState: Record<IncidentState, Badge> = {
  fine: { label: 'Fine', tone: 'good' },
  medium: { label: 'Medium', tone: 'warn' },
  critical: { label: 'Critical', tone: 'bad' },
}

/** Incident feed tier sections (C3.4), in feed order. */
export const incidentTier: Record<IncidentTier, Badge> = {
  critical: { label: 'Critical', tone: 'bad' },
  medium: { label: 'Medium', tone: 'warn' },
  old: { label: 'Older than 1 h', tone: 'neutral' },
}

/** Shown on an alert/incident that reached the backend from the Pi's dead-letter queue. */
export const REPLAYED_LABEL = 'Recovered after lost connection'

export const delivery: Record<AlertDelivery, Badge | null> = {
  live: null,
  dlq: { label: REPLAYED_LABEL, tone: 'warn' },
}

/** Display names for `Alert.alert_type` (the Pi rule reasons, C1.4/C3.7). `panic_button` stays
 * for old rows only. Use `alertTypeLabel()`, which falls back to the raw value. */
export const ALERT_TYPE_LABEL: Record<string, string> = {
  eyes_closed: 'Eyes closed',
  yawn_eyes_closed: 'Eyes closed after yawning',
  head_down: 'Head down',
  head_down_eyes_closed: 'Head down, eyes closed',
  no_face: 'Driver not visible',
  panic_button: 'Panic button',
}

export function alertTypeLabel(alertType: string): string {
  return Object.prototype.hasOwnProperty.call(ALERT_TYPE_LABEL, alertType)
    ? ALERT_TYPE_LABEL[alertType]
    : alertType
}

/** A route's status badge; a `cancelled` route that was stopped (`stop_reason` set) reads
 * "Stopped", since that's what the user did. */
export function routeStatusBadge(r: Pick<Route, 'operative_status' | 'stop_reason'>): Badge {
  if (r.operative_status === 'cancelled' && r.stop_reason) {
    return { label: 'Stopped', tone: 'warn' }
  }
  return routeStatus[r.operative_status]
}

/** True when a route currently counts as "on the road" for dashboard tallies. */
export const isActiveRoute = (s: RouteStatus): boolean =>
  s === 'in_progress' || s === 'in_progress_alert'

export const userStatus: Record<'active' | 'inactive', Badge> = {
  active: { label: 'Active', tone: 'good' },
  inactive: { label: 'Inactive', tone: 'neutral' },
}

/** Display name for each `Role` — used by `Access.tsx`'s table/role picker. */
export const roleLabel: Record<Role, string> = {
  root_admin: 'Root admin',
  admin: 'Admin',
  guardian: 'Guardian',
  truck_driver: 'Truck driver',
}
