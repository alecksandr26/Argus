import { MapContainer, Marker, Polyline, Popup, TileLayer, useMap } from 'react-leaflet'
import { divIcon, latLngBounds } from 'leaflet'
import type { DivIcon } from 'leaflet'
import { useEffect, useRef } from 'react'
import { clock, relativeTime } from '../utils/format'
import type { Tone } from '../utils/status'
import type { FleetMapMarker, LatLng, MapPath, MapPin } from '../utils/mapLayers'

export type { FleetMapMarker, MapPath, MapPin } from '../utils/mapLayers'

/**
 * The Leaflet map behind Live operations, the route view and route history (OpenStreetMap
 * tiles). Presentational only: the screens build the layers with `utils/mapLayers.ts` from
 * `/api/live` / `/track` data, and this component just draws them.
 *
 * Layers:
 * - **Truck markers** (`markers`), keyed by route id, coloured by the route's `incident_state`;
 *   a last-known position (no current fix) gets a hollow, dashed "stale" dot.
 * - **GPS paths** (`paths`), one polyline per route.
 * - **Alert pins** (`pins`), small diamonds coloured by severity.
 * - **Focus** (`focus`): "Show on map" on an alert card flies the camera to that alert and
 *   highlights its pin. A new `nonce` re-flies even to the same point.
 *
 * `react-leaflet` 5.x (the React 19 line). `MapContainer` `center`/`zoom`/`bounds` are init-only
 * (not reactive); `Marker`/`Polyline` positions *are* reactive, so markers move and paths grow on
 * every poll without re-creating the map. Leaflet orders pairs `[lat, lng]`; the API's
 * `{lat, lon}` is converted in `mapLayers.ts`. Markers are `divIcon`s (HTML/CSS), which also
 * sidesteps Leaflet's broken-default-marker-image bug with bundlers.
 */

export interface MapFocus {
  position: LatLng
  /** Highlight this pin (an alert id). */
  pinId?: string | null
  /** Changes on every "Show on map" click, so clicking the same alert twice re-flies. */
  nonce: number
}

/** Central-Mexico / Bajío fallback view, used only when there are no markers. */
const FALLBACK_CENTER: LatLng = [20.6, -100.4]
const FALLBACK_ZOOM = 6
/** How close "Show on map" zooms in (never zooms out a closer view). */
const FOCUS_ZOOM = 14
/** The GPS path colour (the accent token, as a literal: SVG stroke attributes can't read CSS
 * variables). */
const PATH_COLOR = '#47c1e6'

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

/** A coloured dot + plate chip per truck; `--stale` for a last-known position. */
function buildIcon(m: FleetMapMarker): DivIcon {
  const bad = m.tone === 'bad'
  return divIcon({
    className: 'fleet-marker',
    html:
      `<span class="fleet-marker__dot fleet-marker__dot--${m.tone}` +
      `${m.lastKnown ? ' fleet-marker__dot--stale' : ''}"></span>` +
      `<span class="mono fleet-marker__plate${bad ? ' fleet-marker__plate--bad' : ''}">` +
      `${escapeHtml(m.plate)}${m.lastKnown ? ' · last known' : ''}</span>`,
    iconSize: [104, 34],
    iconAnchor: [52, 8],
    popupAnchor: [0, -8],
  })
}

function buildPinIcon(p: MapPin, focused: boolean): DivIcon {
  return divIcon({
    className: 'alert-pin-icon',
    html: `<span class="alert-pin alert-pin--${p.tone}${focused ? ' alert-pin--focused' : ''}"></span>`,
    iconSize: [16, 16],
    iconAnchor: [8, 8],
    popupAnchor: [0, -8],
  })
}

/**
 * Zooms to a single followed marker. `MapContainer`'s `bounds` only fit whatever markers existed
 * on the very first render (react-leaflet builds the Leaflet map once), and the route view's
 * position loads asynchronously, so this is what actually frames the truck: the first appearance
 * snaps with `setView`, later updates ease with `flyTo` at the current zoom (a manual zoom
 * survives the follow).
 */
function FollowMarker({ position, maxZoom }: { position: LatLng; maxZoom: number }) {
  const map = useMap()
  const mounted = useRef(false)
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true
      map.setView(position, maxZoom)
      return
    }
    map.flyTo(position, map.getZoom(), { duration: 0.8 })
    // Keyed on the raw lat/lng, not the tuple's identity; `map`/`maxZoom` are stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [position[0], position[1]])
  return null
}

/** Flies to a "Show on map" target. */
function FocusOn({ focus }: { focus: MapFocus }) {
  const map = useMap()
  useEffect(() => {
    map.flyTo(focus.position, Math.max(map.getZoom(), FOCUS_ZOOM), { duration: 0.8 })
    // Re-fly on every click (nonce), even to the same point.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus.nonce])
  return null
}

export default function FleetMap({
  markers,
  paths = [],
  pins = [],
  focus = null,
  maxZoom = 9,
  follow = false,
}: {
  markers: FleetMapMarker[]
  paths?: MapPath[]
  pins?: MapPin[]
  focus?: MapFocus | null
  /** Cap on how far the first framing may zoom in: 9 suits the fleet-wide view; the route view
   * passes 14 (enough road context without GPS jitter reading as the dot hopping). The map's
   * own interactive ceiling stays higher. */
  maxZoom?: number
  /** Keep the viewport on `markers[0]` as it moves (the route view). Single-marker maps only.
   * Ignored while a "Show on map" focus is set, so the camera doesn't snap back to the truck. */
  follow?: boolean
}) {
  const framing = markers.length
    ? markers.map((m) => m.position)
    : paths.flatMap((p) => p.positions)
  const bounds = framing.length ? latLngBounds(framing) : undefined

  return (
    <MapContainer
      center={FALLBACK_CENTER}
      zoom={FALLBACK_ZOOM}
      bounds={bounds}
      boundsOptions={{ padding: [48, 48], maxZoom }}
      minZoom={5}
      maxZoom={18}
      scrollWheelZoom
      attributionControl
      style={{ position: 'absolute', inset: 0 }}
    >
      <TileLayer
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        maxZoom={19}
      />

      {follow && !focus && markers.length === 1 && (
        <FollowMarker position={markers[0].position} maxZoom={maxZoom} />
      )}
      {focus && <FocusOn focus={focus} />}

      {paths.map((p) => (
        <Polyline
          key={`path-${p.id}`}
          positions={p.positions}
          pathOptions={{ color: PATH_COLOR, weight: 3, opacity: 0.75 }}
        />
      ))}

      {pins.map((p) => (
        <Marker
          key={`pin-${p.id}`}
          position={p.position}
          icon={buildPinIcon(p, focus?.pinId === p.id)}
          zIndexOffset={focus?.pinId === p.id ? 1000 : 0}
        >
          <Popup>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              <span style={{ fontWeight: 600, fontSize: 12.5, color: TONE_VAR[p.tone] }}>
                {p.title}
              </span>
              <span className="mono" style={{ color: 'var(--text-faint)' }}>
                {clock(p.timestamp)} · {relativeTime(p.timestamp)}
              </span>
              {p.notes.map((n) => (
                <span key={n} style={{ color: 'var(--text-soft)' }}>
                  {n}
                </span>
              ))}
            </div>
          </Popup>
        </Marker>
      ))}

      {markers.map((m) => (
        <Marker key={m.id} position={m.position} icon={buildIcon(m)} zIndexOffset={500}>
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
                <span style={{ fontSize: 10.5, fontWeight: 600, color: TONE_VAR[m.tone] }}>
                  {m.stateLabel}
                </span>
              </div>
              <span style={{ color: 'var(--text-soft)' }}>{m.driver}</span>
              <span style={{ color: 'var(--text-faint)' }}>
                {m.origin} → {m.destination}
              </span>
              {m.positionLabel && (
                <span style={{ color: 'var(--warn)', fontWeight: 500 }}>{m.positionLabel}</span>
              )}
              <span className="mono" style={{ color: 'var(--text-faint)' }}>
                {m.speedKmh == null ? '— km/h' : `${m.speedKmh} km/h`}
                {m.vigilanceLabel ? ` · ${m.vigilanceLabel}` : ''}
                {m.timestamp ? ` · ${relativeTime(m.timestamp)}` : ''}
              </span>
            </div>
          </Popup>
        </Marker>
      ))}
    </MapContainer>
  )
}
