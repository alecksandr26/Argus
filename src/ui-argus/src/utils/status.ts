/**
 * Maps the domain status unions (see `src/types.ts`) to an English label and a
 * visual "tone" the `<StatusPill>` / stat tiles render. Kept in one place so the
 * colour language stays consistent with the mockups across every screen.
 */

import type {
  AlertSeverity,
  DriverStatus,
  Role,
  RouteStatus,
  TruckStatus,
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
