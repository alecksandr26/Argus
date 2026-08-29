/**
 * TypeScript shapes for the Argus domain entities.
 *
 * Field names are copied verbatim from the ER model
 * (`docs/designs/ER-model.drawio.xml` / the Lucid JSON export) so that when the
 * FastAPI backend and its Pydantic models exist, these line up 1:1 and the only
 * work is deleting the fixtures — see INTEGRATION.md gap #2. That's also why a
 * couple of names look "wrong" (`reviwed_by_operator`, `blod_type` in the source
 * model): they're kept as the model spells them, with the corrected spelling
 * only where the model itself already uses it.
 *
 * The `*_status` string unions below do NOT exist in the ER model (it just says
 * `operative_status` with no enumerated values). They're a frontend-side guess
 * for the scaffold and must be reconciled with the backend before this is real.
 */

export type Role = 'guard' | 'admin'

export interface User {
  id_user: string
  email: string
  role: Role
  first_name: string
  last_name: string
  phone_number: string
  is_active: boolean
  last_login: string | null
}

export type TruckStatus = 'active' | 'alert' | 'maintenance' | 'inactive'

export interface Truck {
  id_truck: string
  plate_number: string
  brand: string
  model: string
  company_number: string
  raspberry_pi_mac: string | null
  esp32_id: string | null
  operative_status: TruckStatus
  created_at: string
  updated_at: string
}

export type DriverStatus = 'on_route' | 'on_route_alert' | 'resting' | 'inactive'

export interface Driver {
  id_driver: string
  first_name: string
  last_name: string
  license_number: string
  license_expiration: string
  phone_number: string
  emergency_contact_name: string
  emergency_contact_phone: string
  operative_status: DriverStatus
  /** ER model spells this `blod_type`; corrected here. */
  blood_type: string
  created_at: string
  updated_at: string
}

export type RouteStatus =
  | 'in_progress'
  | 'in_progress_alert'
  | 'scheduled'
  | 'completed'
  | 'cancelled'

export interface Coordinates {
  lat: number
  lon: number
}

export interface Route {
  id_route: string
  id_driver: string
  id_truck: string
  origin_name: string
  destination_name: string
  destination_coordinates: Coordinates
  estimated_departure: string
  estimated_arrival: string | null
  actual_departure: string | null
  actual_arrival: string | null
  operative_status: RouteStatus
  created_at: string
  updated_at: string
}

/** Live per-route telemetry — one row per active Route, newest wins. */
export type LiveVigilance = 'normal' | 'low_vigilance' | 'critical'

export interface StatusRoute {
  id_status_route: string
  id_route: string
  current_coordinates: Coordinates
  current_speed: number
  odometer: number
  /** Drowsiness class coming off the edge pipeline (see top-level CLAUDE.md). */
  vigilance: LiveVigilance
  timestamp: string
}

export type AlertSeverity = 'critical' | 'medium' | 'low'

export interface AlertAiMetadata {
  /** Which model produced the classification, for the triage readout. */
  model: string
  /** Softmax over the 3 drowsiness classes, 0..1. */
  scores: { alert: number; low_vigilance: number; drowsy: number }
  clip_seconds: number
}

export interface Alert {
  id_alert: string
  id_route: string
  alert_type: string
  severity_level: AlertSeverity
  ai_metadata: AlertAiMetadata
  media_url: string | null
  coordinates: Coordinates
  speed_at_event: number
  timestamp: string
  /** ER model spelling kept on purpose. */
  reviwed_by_operator: boolean
  operator_notes: string
}
