/**
 * Shared test builders for the C3 shapes (`Alert`, `Incident`, `RouteLive`, `TrackPoint`,
 * `LiveResponse`). Every builder returns a complete, valid object; tests override only the
 * fields they care about.
 */

import type { Alert, Incident, LiveResponse, Route, RouteLive, TrackPoint } from '../types'

export function makeAlert(over: Partial<Alert> = {}): Alert {
  return {
    id_alert: 'a-1',
    id_route: 'r-1',
    alert_type: 'eyes_closed',
    severity_level: 'medium',
    source: 'fusion',
    episode: 'ep-1',
    id_incident: 'i-1',
    delivery: 'live',
    queued_seconds: 0,
    ai_metadata: null,
    grip_status: null,
    related_alert_id: null,
    driver_ok_at: null,
    closed_at: null,
    original_severity_level: null,
    deescalated_by: null,
    deescalated_at: null,
    deescalation_reason: '',
    media_url: null,
    coordinates: { lat: 20.6597, lon: -103.3496 },
    position_unknown: false,
    location_is_last_known: false,
    location_age_s: 0,
    speed_at_event: 62.5,
    timestamp: '2026-10-23T12:00:00Z',
    received_at: '2026-10-23T12:00:01Z',
    time_source: 'relative',
    location_source: 'gps',
    location_accuracy_m: null,
    client_event_id: 'ev-1',
    reviewed_by_operator: false,
    operator_notes: '',
    ...over,
  }
}

export function makeIncident(over: Partial<Incident> = {}): Incident {
  return {
    id_incident: 'i-1',
    id_route: 'r-1',
    severity: 'medium',
    tier: 'medium',
    first_alert_at: '2026-10-23T12:00:00Z',
    last_alert_at: '2026-10-23T12:00:00Z',
    alert_count: 1,
    driver_ok_at: null,
    has_replayed: false,
    closed_at: null,
    closed_by: null,
    close_note: '',
    route_ended: false,
    truck_plate_number: 'ARG-001',
    driver_full_name: 'Ana Ruiz',
    alerts: [makeAlert()],
    ...over,
  }
}

export function makeRoute(over: Partial<Route> = {}): Route {
  return {
    id_route: 'r-1',
    id_driver: 'd-1',
    id_truck: 't-1',
    origin_name: 'Guadalajara',
    destination_name: 'Querétaro',
    destination_coordinates: { lat: 20.5888, lon: -100.3899 },
    estimated_departure: '2026-10-23T10:00:00Z',
    estimated_arrival: null,
    actual_departure: '2026-10-23T10:05:00Z',
    actual_arrival: null,
    operative_status: 'in_progress',
    incident_state: 'fine',
    last_not_ok_at: null,
    stop_reason: null,
    stopped_by: null,
    stopped_at: null,
    created_at: '2026-10-23T09:00:00Z',
    updated_at: '2026-10-23T09:00:00Z',
    ...over,
  }
}

export function makeRouteLive(over: Partial<RouteLive> = {}): RouteLive {
  return {
    ...makeRoute(over),
    truck_plate_number: 'ARG-001',
    driver_full_name: 'Ana Ruiz',
    latest_position: null,
    ...over,
  }
}

export function makePoint(over: Partial<TrackPoint> = {}): TrackPoint {
  return {
    id_route: 'r-1',
    t: '2026-10-23T12:00:00Z',
    lat: 20.6597,
    lon: -103.3496,
    speed: 60,
    vigilance: 'low',
    last_known: false,
    location_age_s: 0,
    ...over,
  }
}

export function makeLive(over: Partial<LiveResponse> = {}): LiveResponse {
  return {
    server_time: '2026-10-23T12:00:05Z',
    routes: [makeRouteLive()],
    incidents: [],
    track: [],
    ...over,
  }
}

/** `2026-10-23T12:00:SSZ` — a point time `s` seconds after noon. */
export function at(s: number): string {
  return new Date(Date.parse('2026-10-23T12:00:00Z') + s * 1000).toISOString()
}
