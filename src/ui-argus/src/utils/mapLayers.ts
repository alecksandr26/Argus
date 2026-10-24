/**
 * What the fleet map draws, built from `/api/live` state (C3.6). Pure functions, so the map rules
 * are unit-tested without Leaflet:
 *
 * - **Truck markers**, one per route, keyed by `id_route`. The colour is the route's
 *   `incident_state` (C3.1/C3.5: fine / medium / critical), never the newest point's vigilance:
 *   a route keeps its state after a medium/critical until a guardian closes the incident or 1 h of
 *   only-OK points clears it. A route whose newest point is a last-known position (no current
 *   fix) gets the "stale" marker style and its "Last known (N old)" label.
 * - **GPS paths**, one polyline per route, from its track points. Last-known points are left out
 *   of the line (they repeat an old fix, so they would draw a jump back); a route needs at least
 *   two live points for a line.
 * - **Alert pins**, one per alert that has a position, coloured by its severity.
 */

import { alertTypeLabel, incidentState, severity, vigilance, type Tone } from './status'
import { alertPosition, pointPosition } from './position'
import { toLatLng } from './geo'
import type { Alert, Incident, RouteLive, TrackPoint } from '../types'

export type LatLng = [number, number]

export interface FleetMapMarker {
  /** The route id: one marker per route. */
  id: string
  position: LatLng
  plate: string
  driver: string
  origin: string
  destination: string
  /** km/h, `null` when unknown. */
  speedKmh: number | null
  /** The route's alert state, e.g. "Critical" (C3.1 `incident_state`). */
  stateLabel: string
  /** The newest point's vigilance, e.g. "Normal" / "Unknown". */
  vigilanceLabel: string | null
  tone: Tone
  /** ISO time of the newest point, shown as relative time in the popup. */
  timestamp: string | null
  /** `true` = the newest point is the device's last known position, not a live fix. */
  lastKnown: boolean
  /** "Last known (3 min old)" when `lastKnown`, else `null`. */
  positionLabel: string | null
}

export interface MapPath {
  /** The route id. */
  id: string
  positions: LatLng[]
}

export interface MapPin {
  /** The alert id. */
  id: string
  position: LatLng
  tone: Tone
  /** "Eyes closed · Critical" */
  title: string
  /** ISO time of the alert. */
  timestamp: string
  /** "Last known (3 min old)" / "Recovered after lost connection" lines, if any. */
  notes: string[]
}

/** A route's truck marker, or `null` when the route has no position yet. */
export function routeMarker(r: RouteLive): FleetMapMarker | null {
  const p = r.latest_position
  if (!p) return null
  const pos = pointPosition(p)
  const state = incidentState[r.incident_state]
  return {
    id: r.id_route,
    position: [p.lat, p.lon],
    plate: r.truck_plate_number ?? '—',
    driver: r.driver_full_name ?? '—',
    origin: r.origin_name,
    destination: r.destination_name,
    speedKmh: p.last_known ? null : p.speed,
    stateLabel: state.label,
    vigilanceLabel: vigilance[p.vigilance].label,
    tone: state.tone,
    timestamp: p.t,
    lastKnown: pos.kind === 'last_known',
    positionLabel: pos.label,
  }
}

export function routeMarkers(routes: readonly RouteLive[]): FleetMapMarker[] {
  return routes.map(routeMarker).filter((m): m is FleetMapMarker => m !== null)
}

/** One route's polyline from its (`t`-ascending) points, or `null` with fewer than two live
 * points. */
export function trackPath(id: string, points: readonly TrackPoint[]): MapPath | null {
  const positions: LatLng[] = points.filter((p) => !p.last_known).map((p) => [p.lat, p.lon])
  return positions.length >= 2 ? { id, positions } : null
}

export function trackPaths(track: Record<string, TrackPoint[]>): MapPath[] {
  return Object.entries(track)
    .map(([id, points]) => trackPath(id, points))
    .filter((p): p is MapPath => p !== null)
}

/** The pin of one alert, or `null` when its position is unknown. */
export function alertPin(a: Alert): MapPin | null {
  const pos = alertPosition(a)
  if (!pos.coordinates) return null
  const sev = severity[a.severity_level]
  const notes: string[] = []
  if (pos.label) notes.push(pos.label)
  if (a.delivery === 'dlq') notes.push('Recovered after lost connection')
  return {
    id: a.id_alert,
    position: toLatLng(pos.coordinates),
    tone: sev.tone,
    title: `${alertTypeLabel(a.alert_type)} · ${sev.label}`,
    timestamp: a.timestamp,
    notes,
  }
}

/** Pins for every alert (with a position) of the given incidents. */
export function incidentPins(incidents: readonly Incident[]): MapPin[] {
  return alertPins(incidents.flatMap((i) => i.alerts))
}

/** Pins for alerts, de-duplicated by alert id (first one wins). */
export function alertPins(alerts: readonly Alert[]): MapPin[] {
  const seen = new Set<string>()
  const pins: MapPin[] = []
  for (const a of alerts) {
    if (seen.has(a.id_alert)) continue
    seen.add(a.id_alert)
    const pin = alertPin(a)
    if (pin) pins.push(pin)
  }
  return pins
}
