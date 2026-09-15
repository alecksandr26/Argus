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

/**
 * `root_admin`: owner/bootstrap account, full control including user management.
 * `admin`: operations role (schedule routes, manage the truck/driver roster) — a scoped
 * exception lets it also create/manage `guardian`-role accounts only (see
 * `src/backend-argus/app/routers/users.py`'s per-route scoping); it cannot see or touch
 * `root_admin`/other `admin` accounts at all.
 * `guardian`: read-only safety monitoring + alert review.
 * `truck_driver`: receives alerts/status.
 */
export type Role = 'root_admin' | 'admin' | 'guardian' | 'truck_driver'

/**
 * `POST /api/auth/login`'s response shapes (`src/backend-argus/app/schemas/auth.py`'s
 * `LoginUser`/`LoginResponse`), used by `src/context/AuthContext.tsx` for the stored session.
 * `LoginUser` is deliberately a subset of `User` (no `phone_number`/`is_active`/`last_login`) —
 * mirror the backend exactly rather than reusing `User` here.
 */
export interface LoginUser {
  id_user: string
  email: string
  role: Role
  first_name: string
  last_name: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
  user: LoginUser
}

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

/** The one shared severity scale for both live status and alerts (backend-argus's `Severity`) —
 * previously two separate, mismatched 3-tier enums (`StatusRoute` had its own `normal`/
 * `low_vigilance`/`critical`, `Alert` had `critical`/`medium`/`low`). Unified so a truck's live
 * status and its alert history are directly comparable. */
export type AlertSeverity = 'critical' | 'medium' | 'low'

export interface StatusRoute {
  id_status_route: string
  id_route: string
  current_coordinates: Coordinates
  current_speed: number
  odometer: number
  /** Shares `AlertSeverity`'s exact scale — see that type's doc comment. Mirrors the most
   * recently posted `Alert.severity_level` for this truck's route (see
   * `src/esp32-argus/README.md`'s fusion section), not a separate drowsiness-only reading. */
  vigilance: AlertSeverity
  timestamp: string
}

/**
 * `GET /api/routes/active`'s response shape — a `Route` embedding its newest `Status_Route`
 * snapshot plus light truck/driver refs, built specifically for `LiveOps.tsx` so it doesn't
 * need a follow-up fetch per marker. See `src/backend-argus/CLAUDE.md`'s "routes/active"
 * section.
 */
export interface RouteWithStatus extends Route {
  latest_status: StatusRoute | null
  truck_plate_number: string | null
  driver_full_name: string | null
}

/** Which decision path produced an `Alert`: the ESP32's fused drowsy+grip evaluation, or the
 * panic button (unconditional `critical`, no debounce) — see `src/esp32-argus/README.md`'s
 * fusion section and `src/cv-argus/src/orchestrator/fusion_contract.py`'s reference decision
 * logic. */
export type AlertSource = 'fusion' | 'panic_button'

/** The grip sensor's contribution to a `fusion` alert — `null` for `panic_button` alerts, since
 * no camera/grip evaluation happens for those. */
export type GripStatus = 'good' | 'bad'

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
  source: AlertSource
  /** Required (non-null) when `source === 'fusion'`, `null` when `source === 'panic_button'` —
   * see backend-argus's `validate_source_ai_metadata`. */
  ai_metadata: AlertAiMetadata | null
  /** Required (non-null) when `source === 'fusion'`, `null` when `source === 'panic_button'`. */
  grip_status: GripStatus | null
  /** Set when this alert is an escalation of a still-open incident (e.g. a `medium` fusion
   * alert that didn't improve within the escalation window) — points back at the original
   * alert's `id_alert` rather than that alert being mutated in place. */
  related_alert_id: string | null
  /** Set once the underlying condition recovers (both signals good, held for the recovery
   * window) — `null` while the incident is still open. */
  resolved_at: string | null
  media_url: string | null
  coordinates: Coordinates
  speed_at_event: number
  timestamp: string
  reviewed_by_operator: boolean
  operator_notes: string
}
