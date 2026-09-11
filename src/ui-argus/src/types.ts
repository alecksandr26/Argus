/**
 * TypeScript shapes for the Argus domain entities.
 *
 * Field names line up 1:1 with `src/backend-argus/app/models/*.py` and its Pydantic
 * schemas, now that the backend exists — see that module's CLAUDE.md for the full
 * old (ER-diagram) -> new field-name table. The ER diagram itself
 * (`docs/designs/ER-model.drawio.xml`) had a couple of typos (`reviwed_by_operator`,
 * `blod_type`) that are now corrected everywhere, here included; treat the diagram
 * as historical, not the live source of truth.
 *
 * The `*_status` string unions below still do NOT exist in the ER model (it just
 * says `operative_status` with no enumerated values) — they remain a frontend-side
 * guess pending backend confirmation, except `Role`, which now matches the
 * backend's `Role` enum exactly (see below).
 */

export type Role = 'root_admin' | 'guardian' | 'truck_driver'

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
  /** Which model produced the classification, for the triage readout. Nullable: the deployed
   * cv-argus pipeline doesn't currently expose a per-alert model-version string — see the
   * backend CLAUDE.md's "Coordination note". */
  model: string | null
  /** Probability of Drowsy vs Not Drowsy — the project's binary scheme (root CLAUDE.md's
   * drowsiness-class migration), not the old 3-class Alert/Low Vigilant/Drowsy split. */
  scores: { not_drowsy: number; drowsy: number }
  /** Nullable for the same reason as `model` — no discrete clip duration in the current
   * pipeline (a rolling window, not a fixed-length clip). */
  clip_seconds: number | null
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
  reviewed_by_operator: boolean
  operator_notes: string
}
