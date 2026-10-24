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
  /** Whether an edge-device key is currently issued (the key itself is never returned). */
  has_device_key: boolean
  device_key_issued_at: string | null
  /** Last time a request authenticated with this truck's device key (throttled to ~1 min). */
  device_last_seen_at: string | null
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
  /** Drives the map marker colour (C3.1/C3.5). Raised only by medium/critical alerts; lowered by
   * closing incidents or by 1 h of only-OK GPS points. The UI never recomputes it. */
  incident_state: IncidentState
  /** Newest `occurred_at` of a medium/critical alert or point on this route. */
  last_not_ok_at: string | null
  /** Set by `POST /api/routes/{id}/stop` ("Stop route"; the status becomes `cancelled`). */
  stop_reason: string | null
  /** User id of whoever stopped the route. */
  stopped_by: string | null
  stopped_at: string | null
  created_at: string
  updated_at: string
}

/** A route's alert state for its map marker (C3.1): `fine` = no medium/critical outstanding. */
export type IncidentState = 'fine' | 'medium' | 'critical'

/** The one shared severity scale for both live status and alerts (backend-argus's `Severity`) —
 * previously two separate, mismatched 3-tier enums (`StatusRoute` had its own `normal`/
 * `low_vigilance`/`critical`, `Alert` had `critical`/`medium`/`low`). Unified so a truck's live
 * status and its alert history are directly comparable. */
export type AlertSeverity = 'critical' | 'medium' | 'low'

/** `relative` = computed by the backend from a device `age_s` (every report event, C2.1). */
export type TimeSource = 'gps' | 'network' | 'server' | 'relative'
export type LocationSource = 'gps' | 'cell'

/** A GPS point's vigilance (C3.3): the latest Pi reading when the ESP32 took the point, or
 * `unknown` when no Pi record arrived recently (or none since boot). `unknown` is the backend
 * default — a point is never assumed `low`. */
export type Vigilance = 'low' | 'medium' | 'critical' | 'unknown'

export interface StatusRoute {
  id_status_route: string
  id_route: string
  current_coordinates: Coordinates
  current_speed: number
  odometer: number
  vigilance: Vigilance
  /** `true` = the ESP32 had no fix; this is its saved last good position. */
  location_is_last_known: boolean
  /** Age of that fix at the point's time, seconds (`0`/`null` for a live fix). */
  location_age_s: number | null
  /** When the event happened (device GPS/network clock), else when the backend received it. */
  timestamp: string
  /** When the backend received it; later than `timestamp` for records relayed from a backlog. */
  received_at: string
  /** Which clock `timestamp` came from. */
  time_source: TimeSource
  /** `cell` = approximate cell-tower position (no GPS fix); see `location_accuracy_m`. */
  location_source: LocationSource
  location_accuracy_m: number | null
}

/** A GPS point as `GET /api/live` / `GET /api/routes/{id}/track` return it (C3.6). */
export interface LivePosition {
  /** The point's time (ISO). */
  t: string
  lat: number
  lon: number
  /** km/h; `0` for a last-known point. */
  speed: number
  vigilance: Vigilance
  /** `true` = no current fix, this is the last good position. */
  last_known: boolean
  location_age_s: number | null
}

export interface TrackPoint extends LivePosition {
  id_route: string
}

/** One route in `GET /api/live`'s `routes` (C3.6): a `Route` plus its newest point. */
export interface RouteLive extends Route {
  truck_plate_number: string | null
  driver_full_name: string | null
  latest_position: LivePosition | null
}

/** Feed tier (C3.4), computed by the server at response time: `old` = more than 1 h since the
 * incident's last alert, else its severity. The UI trusts it and never recomputes it. */
export type IncidentTier = 'critical' | 'medium' | 'old'

/** Medium/critical alerts of one route less than 15 min apart, closed together by a guardian or
 * root_admin (C3.4). */
export interface Incident {
  id_incident: string
  id_route: string
  /** Max `severity_level` of its alerts. */
  severity: 'medium' | 'critical'
  tier: IncidentTier
  first_alert_at: string
  last_alert_at: string
  alert_count: number
  /** When the driver was OK again (episode of its newest alert). It never closes anything. */
  driver_ok_at: string | null
  /** Any of its alerts was replayed from the DLQ ("recovered after lost connection"). */
  has_replayed: boolean
  closed_at: string | null
  /** User id. */
  closed_by: string | null
  /** `''` when none. */
  close_note: string
  /** The route is completed or cancelled now. */
  route_ended: boolean
  truck_plate_number: string | null
  driver_full_name: string | null
  /** Timestamp ascending, full alerts (each with its position). */
  alerts: Alert[]
}

/** `GET /api/live` (C3.6). Incidents and routes come in full every time; `track` is a delta
 * since the `since` cursor (the previous response's `server_time`). */
export interface LiveResponse {
  /** Pass back as `since` on the next poll. */
  server_time: string
  routes: RouteLive[]
  /** All open incidents in scope, feed-ordered (tier, then `last_alert_at` descending). */
  incidents: Incident[]
  /** Ordered by `id_route`, then `t` ascending. */
  track: TrackPoint[]
}

/** `GET /api/routes/{id}/track` (C3.6): a route's whole path, downsampled past `max_points`. */
export interface RouteTrack {
  id_route: string
  downsampled: boolean
  points: TrackPoint[]
}

/** Which decision path produced an `Alert`. Every new alert is `fusion`; `panic_button` exists
 * only on old rows (the panic button was removed and the backend 422s a new one), so it stays
 * here purely so those rows still read. */
export type AlertSource = 'fusion' | 'panic_button'

/** The grip sensor's contribution to an old `fusion` alert. No longer sent: `null` on new
 * alerts. */
export type GripStatus = 'good' | 'bad'

/** How an alert reached the backend (C3.2): `live`, or replayed from the Pi's dead-letter queue
 * after a lost connection (`dlq`, shown as "Recovered after lost connection"). */
export type AlertDelivery = 'live' | 'dlq'

export interface AlertAiMetadata {
  /** Which model produced the classification, for the triage readout. Nullable: the deployed
   * cv-argus pipeline doesn't always expose a per-alert model-version string. */
  model: string | null
  // The backend also sends `scores` ({not_drowsy, drowsy}). Not typed on purpose: cv-argus
  // fills them with a fixed value per severity (RULE_P_DROWSY), not a model probability.
}

export interface Alert {
  id_alert: string
  id_route: string
  /** The Pi rule `reason` (`eyes_closed`, `no_face`, ...): show it through `alertTypeLabel()`. */
  alert_type: string
  severity_level: AlertSeverity
  source: AlertSource
  /** The Pi episode this alert belongs to (an escalation shares its medium's episode). */
  episode: string | null
  /** `null` only for `low` alerts (old rows). */
  id_incident: string | null
  delivery: AlertDelivery
  /** Seconds the event waited in the Pi's dead-letter queue (`0` for `live`). */
  queued_seconds: number
  /** May be `null` (an alert with no model output). */
  ai_metadata: AlertAiMetadata | null
  /** `null` on every new alert (grip is no longer sent); set only on old rows. */
  grip_status: GripStatus | null
  /** Set when this alert is an escalation of the same episode — points back at the original
   * alert's `id_alert` rather than that alert being mutated in place. */
  related_alert_id: string | null
  /** When the device said the driver was OK again. Informational only: it never closes the
   * alert. Replaces the retired `resolved_at`. */
  driver_ok_at: string | null
  /** Set when a guardian/root_admin closed this alert's incident. */
  closed_at: string | null
  /** Set by a guardian/root_admin's manual de-escalation (`POST /api/alerts/:id/de-escalate`):
   * the severity the device originally reported. `severity_level` then holds the lowered one.
   * `null` if never de-escalated. */
  original_severity_level: AlertSeverity | null
  deescalated_by: string | null
  deescalated_at: string | null
  deescalation_reason: string
  media_url: string | null
  /** `null` = position unknown (the device had neither a fix nor a saved last-known one). */
  coordinates: Coordinates | null
  /** `true` iff `coordinates` is `null`. */
  position_unknown: boolean
  /** `true` = `coordinates` is the last known fix, not a live one (see `location_age_s`). */
  location_is_last_known: boolean
  /** Age of that fix at the event time, seconds. */
  location_age_s: number | null
  /** km/h, or `null` when unknown. */
  speed_at_event: number | null
  /** When the event happened (`occurred_at`). */
  timestamp: string
  /** When the backend received it; later than `timestamp` for records relayed from a backlog. */
  received_at: string
  /** Which clock `timestamp` came from. */
  time_source: TimeSource
  /** `cell` = approximate cell-tower position (no GPS fix); see `location_accuracy_m`. */
  location_source: LocationSource
  location_accuracy_m: number | null
  client_event_id: string | null
  reviewed_by_operator: boolean
  operator_notes: string
}
