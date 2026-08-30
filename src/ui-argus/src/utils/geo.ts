import type { Coordinates } from '../types'

/**
 * Coordinate adapter for the map.
 *
 * `Coordinates` (`{ lat, lon }`, WGS84 decimal degrees) is the app's ONE
 * canonical shape — it matches the ER model's field spelling and is what every
 * screen and `FleetMap` consume. Backends serialise a geographic point in
 * several different ways, so `normalizeCoordinates()` converts any of them into
 * `Coordinates` **at the API boundary**: call it once, in the API client, on
 * every coordinate field — never sprinkle shape checks through the components.
 *
 * Shapes it accepts:
 *
 *  | # | Input                                             | Notes                                              |
 *  |---|---------------------------------------------------|----------------------------------------------------|
 *  | 1 | `{ lat: 20.58, lon: -100.38 }`                   | the ER-model / current-fixtures shape (passthrough) |
 *  | 2 | `{ lat: 20.58, lng: -100.38 }`                   | the JS / Leaflet spelling of "lon"                 |
 *  | 3 | `[-100.38, 20.58]`                               | a bare GeoJSON position — **longitude first**      |
 *  | 4 | `{ type: "Point", coordinates: [-100.38, 20.58] }` | a GeoJSON Point geometry — the shape MongoDB's    |
 *  |   |                                                 | `2dsphere` geo-index stores, so a FastAPI + Mongo  |
 *  |   |                                                 | backend is the likely source of this one          |
 *  | 5 | `"20.58,-100.38"`                                | a `"lat,lon"` string                              |
 *
 * A GeoJSON position may carry a third element (elevation) — it is ignored.
 * `normalizeCoordinates` throws on an unrecognised shape or an out-of-range
 * value, so a malformed payload fails loudly at the boundary instead of quietly
 * dropping a marker at (0, 0) off the coast of Africa.
 *
 * The Argus fields that carry coordinates (per the ER model): `Alert.coordinates`,
 * `Status_Route.current_coordinates`, `Route.destination_coordinates`.
 */

/** A GeoJSON position: `[longitude, latitude]` (that order). Elevation, if present, is dropped. */
export type GeoJsonPosition = [number, number] | [number, number, number]

/** A GeoJSON Point geometry. */
export interface GeoJsonPoint {
  type: 'Point'
  coordinates: GeoJsonPosition
}

/** Every coordinate shape `normalizeCoordinates` understands. */
export type RawCoordinates =
  | Coordinates
  | { lat: number; lng: number }
  | GeoJsonPosition
  | GeoJsonPoint
  | string

/** Backend coordinate payload (any supported shape) → the app's canonical `Coordinates`. */
export function normalizeCoordinates(raw: unknown): Coordinates {
  // 4 — GeoJSON Point geometry: { type: 'Point', coordinates: [lng, lat] }
  if (isRecord(raw) && raw.type === 'Point') {
    return fromGeoJsonPosition(raw.coordinates)
  }
  // 3 — bare GeoJSON position: [lng, lat]
  if (Array.isArray(raw)) {
    return fromGeoJsonPosition(raw)
  }
  // 1 / 2 — { lat, lon } or { lat, lng }
  if (isRecord(raw) && typeof raw.lat === 'number') {
    const lon =
      typeof raw.lon === 'number'
        ? raw.lon
        : typeof raw.lng === 'number'
          ? raw.lng
          : undefined
    if (lon !== undefined) return assertInRange({ lat: raw.lat, lon })
  }
  // 5 — "lat,lon"
  if (typeof raw === 'string') {
    const parts = raw.split(',').map((s) => s.trim())
    if (parts.length === 2 && parts[0] !== '' && parts[1] !== '') {
      const lat = Number(parts[0])
      const lon = Number(parts[1])
      if (Number.isFinite(lat) && Number.isFinite(lon)) {
        return assertInRange({ lat, lon })
      }
    }
  }
  throw new Error(
    `normalizeCoordinates: unrecognised coordinate shape ${JSON.stringify(raw)}`,
  )
}

/** Canonical `Coordinates` → the `[lat, lng]` tuple Leaflet / react-leaflet expect. */
export function toLatLng(c: Coordinates): [number, number] {
  return [c.lat, c.lon]
}

// --- internals ----------------------------------------------------------

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null
}

function fromGeoJsonPosition(pos: unknown): Coordinates {
  if (!Array.isArray(pos) || pos.length < 2) {
    throw new Error(
      `normalizeCoordinates: expected a GeoJSON position [lng, lat], got ${JSON.stringify(pos)}`,
    )
  }
  const [lng, lat] = pos
  if (typeof lng !== 'number' || typeof lat !== 'number') {
    throw new Error(
      `normalizeCoordinates: a GeoJSON position must be two numbers, got ${JSON.stringify(pos)}`,
    )
  }
  return assertInRange({ lat, lon: lng })
}

function assertInRange(c: Coordinates): Coordinates {
  if (c.lat < -90 || c.lat > 90 || c.lon < -180 || c.lon > 180) {
    throw new Error(
      `normalizeCoordinates: out-of-range (lat ${c.lat}, lon ${c.lon}) — ` +
        `if lat looks like a longitude, the source is probably [lng, lat] and the order was swapped`,
    )
  }
  return c
}
