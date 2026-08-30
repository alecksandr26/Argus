import { MapContainer, Marker, Popup, TileLayer } from 'react-leaflet'
import { divIcon, latLngBounds } from 'leaflet'
import type { DivIcon } from 'leaflet'
import { relativeTime } from '../utils/format'
import type { Tone } from '../utils/status'

/**
 * The Live operations fleet map. Replaces the earlier hand-drawn SVG projection
 * with a real Leaflet map (OpenStreetMap tiles). Presentational only — `LiveOps`
 * builds the marker array from fixtures; this component just renders it.
 *
 * `react-leaflet` 5.x (the React 19 line). `MapContainer` `center`/`zoom`/
 * `bounds` are init-only (not reactive); `Marker` `position` *is* reactive, so
 * when live data replaces the fixtures the markers move for free and only a
 * bounds re-fit needs a `useMap()` child effect.
 *
 * Coordinates: the `{ lat, lon }` values in `src/data/fixtures.ts` are real
 * WGS84 decimal degrees — the cities (`CITY` table) are the actual positions of
 * real places in central Mexico; the per-truck `current_coordinates` and the
 * per-alert `coordinates` are hand-placed points along the real highway
 * corridors between them (invented demo telemetry, real coordinate system).
 * Leaflet orders pairs `[lat, lng]`; `LiveOps` converts each `Coordinates` with
 * `toLatLng` from `src/utils/geo.ts`, which is also where `normalizeCoordinates`
 * (the API-boundary adapter for GeoJSON / `{lat,lng}` / string payloads) lives.
 *
 * How Leaflet places geometry — anything geographic takes a "LatLngExpression"
 * (`L.latLng(lat,lng)`, `[lat,lng]`, or `{lat,lng}`):
 *   - `<Marker position icon>` — a pin. `icon` is `L.icon({iconUrl,…})` (image),
 *     `L.divIcon({html,className,…})` (HTML/CSS — used here, and it sidesteps the
 *     broken-default-marker-image bug), or the default teardrop if omitted.
 *   - `L.circleMarker` (pixel radius) / `L.circle` (metre radius) — vector dots.
 *   - `<Polyline positions>` — a line; this is how the OSRM route renders later.
 *   - `<Polygon>` / `L.rectangle` — areas (geofences).
 *   - `L.geoJSON(featureCollection)` — render a whole GeoJSON payload at once.
 *   - `map.setView` / `map.fitBounds(L.latLngBounds([...]))` / `map.flyTo` — move
 *     the camera; `fitBounds` is what frames the fleet on load below.
 */

export interface FleetMapMarker {
  id: string
  /** [lat, lng] — Leaflet's order, converted from the ER model's `{ lat, lon }`. */
  position: [number, number]
  plate: string
  driver: string
  origin: string
  destination: string
  speedKmh: number
  vigilanceLabel: string
  tone: Tone
  /** ISO timestamp of the last position fix — shown as relative time in the popup. */
  timestamp: string
}

/** Central-Mexico / Bajío fallback view, used only when there are no markers. */
const FALLBACK_CENTER: [number, number] = [20.6, -100.4]
const FALLBACK_ZOOM = 6

const TONE_VAR: Record<Tone, string> = {
  good: 'var(--good)',
  warn: 'var(--warn)',
  bad: 'var(--bad)',
  neutral: 'var(--text-faint)',
}

const HTML_ESCAPES: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => HTML_ESCAPES[c])
}

/**
 * A custom `divIcon` per truck — a coloured dot + plate chip, matching the
 * approved mockup. Using `divIcon` (never a default `Marker` icon) also
 * sidesteps the well-known Leaflet-with-a-bundler broken-marker-image bug
 * entirely: Leaflet never tries to resolve `marker-icon.png` / `marker-shadow.png`.
 */
function buildIcon(m: FleetMapMarker): DivIcon {
  const bad = m.tone === 'bad'
  return divIcon({
    className: 'fleet-marker',
    html:
      `<span class="fleet-marker__dot fleet-marker__dot--${m.tone}"></span>` +
      `<span class="mono fleet-marker__plate${bad ? ' fleet-marker__plate--bad' : ''}">` +
      `${escapeHtml(m.plate)}</span>`,
    iconSize: [84, 34],
    iconAnchor: [42, 8],
    popupAnchor: [0, -8],
  })
}

export default function FleetMap({ markers }: { markers: FleetMapMarker[] }) {
  const bounds = markers.length
    ? latLngBounds(markers.map((m) => m.position))
    : undefined

  return (
    <MapContainer
      center={FALLBACK_CENTER}
      zoom={FALLBACK_ZOOM}
      bounds={bounds}
      boundsOptions={{ padding: [48, 48], maxZoom: 9 }}
      minZoom={5}
      maxZoom={13}
      scrollWheelZoom
      attributionControl
      style={{ position: 'absolute', inset: 0 }}
    >
      <TileLayer
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        maxZoom={19}
      />

      {markers.map((m) => (
        <Marker key={m.id} position={m.position} icon={buildIcon(m)}>
          <Popup>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'baseline',
                  justifyContent: 'space-between',
                  gap: 10,
                }}
              >
                <span className="mono" style={{ fontWeight: 600, fontSize: 12.5 }}>
                  {m.plate}
                </span>
                <span
                  style={{
                    fontSize: 10.5,
                    fontWeight: 600,
                    color: TONE_VAR[m.tone],
                  }}
                >
                  {m.vigilanceLabel}
                </span>
              </div>
              <span style={{ color: 'var(--text-soft)' }}>{m.driver}</span>
              <span style={{ color: 'var(--text-faint)' }}>
                {m.origin} → {m.destination}
              </span>
              <span className="mono" style={{ color: 'var(--text-faint)' }}>
                {m.speedKmh} km/h · {relativeTime(m.timestamp)}
              </span>
            </div>
          </Popup>
        </Marker>
      ))}
    </MapContainer>
  )
}
