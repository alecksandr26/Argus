/**
 * Maps the domain status unions (see `src/types.ts`) to an English label and a
 * visual "tone" the `<StatusPill>` / stat tiles render. Kept in one place so the
 * colour language stays consistent with the mockups across every screen.
 */

import type {
  AlertSeverity,
  DriverStatus,
  LiveVigilance,
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

export const vigilance: Record<LiveVigilance, Badge> = {
  normal: { label: 'Normal', tone: 'good' },
  low_vigilance: { label: 'Low vigilance', tone: 'warn' },
  critical: { label: 'Critical', tone: 'bad' },
}

export const severity: Record<AlertSeverity, Badge> = {
  critical: { label: 'Critical', tone: 'bad' },
  medium: { label: 'Medium', tone: 'warn' },
  low: { label: 'Low', tone: 'neutral' },
}

/** True when a route currently counts as "on the road" for dashboard tallies. */
export const isActiveRoute = (s: RouteStatus): boolean =>
  s === 'in_progress' || s === 'in_progress_alert'
