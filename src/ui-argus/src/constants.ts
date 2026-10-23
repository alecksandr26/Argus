/**
 * UI-side timing constants (`docs/designs/final-tweaks-contracts.md`, C4). The backend's own
 * constants (`INCIDENT_GROUP_WINDOW`, `OLD_INCIDENT_AFTER`, ...) are deliberately NOT mirrored
 * here: the UI trusts the server's `tier`/`incident_state` instead of recomputing them (C3.6).
 */

/** `GET /api/live` poll interval (Live operations + route view). Paused while the tab is hidden. */
export const UI_POLL_MS = 5000

/** Alert history poll interval. */
export const ALERT_HISTORY_POLL_MS = 5000

/** Simulator status poll interval (only while a session is open). */
export const SIMULATOR_POLL_MS = 10000

/**
 * Most track points the UI keeps per route between `/api/live` polls (newest kept). The ESP32
 * reports one point per second, so a long route would otherwise grow the in-memory path
 * without bound; this keeps about 2 h 45 min of a 1 Hz path on the map. UI-only, not a contract
 * value: the full history of a route is `GET /api/routes/{id}/track`.
 */
export const LIVE_TRACK_MAX_POINTS_PER_ROUTE = 10000
