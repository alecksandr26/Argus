import { useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import PageHeader from '../components/PageHeader'
import FleetMap, { type MapFocus } from '../components/FleetMap'
import FilterChips from '../components/FilterChips'
import IncidentFeed from '../components/IncidentFeed'
import CloseNotice from '../components/CloseNotice'
import RouteEndActions from '../components/RouteEndActions'
import SimulatorControl from '../components/SimulatorControl'
import StatusPill from '../components/StatusPill'
import { useAuth } from '../context/AuthContext'
import { useLive } from '../hooks/useLive'
import { longDay } from '../utils/format'
import { incidentState } from '../utils/status'
import { toLatLng } from '../utils/geo'
import { incidentPins, routeMarkers, trackPaths } from '../utils/mapLayers'
import { pointPosition, positionSummary } from '../utils/position'
import type { Alert, Incident, IncidentTier } from '../types'

/**
 * Live operations, on one poll: `GET /api/live` every 5 s (`useLive`, paused while the tab is
 * hidden; only the data refreshes, never the page). It gives the active routes with their newest
 * position, every open incident in full, and the GPS points since the last poll.
 *
 * - **Map:** one marker per route, coloured by the route's `incident_state` (not by the newest
 *   point), a hollow dashed marker for a last-known position, each route's GPS path, and a pin
 *   per open-incident alert.
 * - **Incidents:** the tiered feed (Critical → Medium → Older than 1 h), grouped by route.
 *   Guardian and root_admin close incidents from it; "Show on map" flies the map to an alert.
 *   After a close, a `role="status"` confirmation ("… incident closed · Closed HH:MM", C3.7)
 *   stays above the panel until the next close or navigation, and the incident leaves the feed
 *   at once (it's hidden locally until the re-poll drops it).
 *   There's no "Reviewed/Unreviewed" filter any more: the feed holds exactly the open incidents,
 *   and a closed incident leaves it for Alert history.
 * - **Routes:** the active routes, with Complete trip / Stop route for root_admin and admin.
 */

const TIER_FILTERS: { key: IncidentTier | 'all'; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'critical', label: 'Critical' },
  { key: 'medium', label: 'Medium' },
  { key: 'old', label: 'Old' },
]

const PANELS: { key: 'incidents' | 'routes'; label: string }[] = [
  { key: 'incidents', label: 'Open incidents' },
  { key: 'routes', label: 'Routes on the road' },
]

const toneColor = {
  good: 'var(--good)',
  warn: 'var(--warn)',
  bad: 'var(--bad)',
  neutral: 'var(--text-faint)',
} as const

export default function LiveOps() {
  const { session } = useAuth()
  const token = session!.token
  const role = session!.user.role

  const { live, error, refresh } = useLive(token)
  const [tierFilter, setTierFilter] = useState<IncidentTier | 'all'>('all')
  const [panel, setPanel] = useState<'incidents' | 'routes'>('incidents')
  const [focus, setFocus] = useState<MapFocus | null>(null)
  const focusNonce = useRef(0)

  // The last close's confirmation, and the incidents closed from this page: hidden from the
  // feed right away, so a poll that was already in flight can't flash them back.
  const [closedNotice, setClosedNotice] = useState<Incident | null>(null)
  const [closedIds, setClosedIds] = useState<ReadonlySet<string>>(() => new Set())
  const openIncidents = useMemo(
    () => live.incidents.filter((i) => !closedIds.has(i.id_incident)),
    [live.incidents, closedIds],
  )
  function onIncidentClosed(inc: Incident) {
    setClosedNotice(inc)
    setClosedIds((prev) => new Set(prev).add(inc.id_incident))
    void refresh()
  }

  const tiles = useMemo(() => {
    const counts = { fine: 0, medium: 0, critical: 0 }
    for (const r of live.routes) counts[r.incident_state]++
    return { onRoute: live.routes.length, ...counts }
  }, [live.routes])

  const markers = useMemo(() => routeMarkers(live.routes), [live.routes])
  const paths = useMemo(() => trackPaths(live.track), [live.track])
  const pins = useMemo(() => incidentPins(openIncidents), [openIncidents])

  const feed = useMemo(
    () => openIncidents.filter((i) => tierFilter === 'all' || i.tier === tierFilter),
    [openIncidents, tierFilter],
  )

  function showOnMap(a: Alert) {
    if (!a.coordinates) return
    focusNonce.current += 1
    setFocus({ position: toLatLng(a.coordinates), pinId: a.id_alert, nonce: focusNonce.current })
  }

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        padding: '24px 28px',
        gap: 20,
        height: '100%',
        overflow: 'hidden',
      }}
    >
      <PageHeader
        title="Live operations"
        subtitle={`${longDay()} · Morning shift 06:00–14:00`}
        actions={
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 7,
              background: 'var(--surface)',
              border: '1px solid var(--border-soft)',
              borderRadius: 8,
              padding: '8px 12px',
            }}
          >
            <span
              style={{
                width: 7,
                height: 7,
                borderRadius: '50%',
                background: error ? 'var(--bad)' : 'var(--good)',
                boxShadow: `0 0 0 3px ${error ? 'var(--bad-soft)' : 'var(--good-soft)'}`,
              }}
            />
            <span style={{ fontSize: 12, color: 'var(--text-soft)' }}>
              {error ?? 'Fleet connection stable'}
            </span>
          </div>
        }
      />

      {/* root_admin only: cloud simulator on/off (10-minute sessions, see SimulatorControl) */}
      {role === 'root_admin' && <SimulatorControl token={token} />}

      {/* stat tiles: routes by their alert state (incident_state) */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 14 }}>
        <Tile label="Trucks on route" value={tiles.onRoute} />
        <Tile label="Normal" value={tiles.fine} color="var(--good)" />
        <Tile label="Medium severity" value={tiles.medium} color="var(--warn)" border="var(--warn)" />
        <Tile
          label="Critical severity"
          value={tiles.critical}
          color="var(--bad)"
          border="var(--bad)"
          bg="var(--bad-soft)"
        />
      </div>

      <div style={{ flex: 1, display: 'flex', gap: 18, minHeight: 0 }}>
        {/* map */}
        <div
          className="panel"
          style={{
            flex: 1.5,
            padding: 16,
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
            overflow: 'hidden',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontSize: 13, fontWeight: 600 }}>
              Fleet map — Central Mexico &amp; Bajío
            </span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--bad)' }} />
              <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>LIVE</span>
            </div>
          </div>

          <div
            style={{
              flex: 1,
              minHeight: 0,
              position: 'relative',
              borderRadius: 8,
              overflow: 'hidden',
              isolation: 'isolate',
              background: '#e8eaed',
            }}
          >
            <FleetMap markers={markers} paths={paths} pins={pins} focus={focus} />
            <MapLegend />
          </div>
        </div>

        {/* incidents / routes */}
        <div
          className="panel"
          style={{
            width: 400,
            flexShrink: 0,
            padding: 16,
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
            overflow: 'hidden',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <FilterChips options={PANELS} value={panel} onChange={setPanel} />
            <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>
              {openIncidents.length} open
            </span>
          </div>

          <CloseNotice incident={closedNotice} />

          {panel === 'incidents' ? (
            <>
              <FilterChips options={TIER_FILTERS} value={tierFilter} onChange={setTierFilter} />
              <div style={{ overflowY: 'auto', minHeight: 0 }}>
                <IncidentFeed
                  incidents={feed}
                  role={role}
                  token={token}
                  onClosed={onIncidentClosed}
                  onShowOnMap={showOnMap}
                  focusedAlertId={focus?.pinId ?? null}
                  emptyLabel={
                    tierFilter === 'all' ? 'No open incidents.' : 'No open incidents in this tier.'
                  }
                />
              </div>
            </>
          ) : (
            <div
              style={{ display: 'flex', flexDirection: 'column', gap: 8, overflowY: 'auto' }}
              data-testid="active-routes"
            >
              {live.routes.map((r) => {
                const pos = r.latest_position ? pointPosition(r.latest_position) : null
                return (
                  <div
                    key={r.id_route}
                    data-testid="active-route"
                    style={{
                      display: 'flex',
                      flexDirection: 'column',
                      gap: 4,
                      padding: 10,
                      borderRadius: 9,
                      background: 'var(--surface-2)',
                      border: '1px solid var(--border-soft)',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                      <Link
                        to={`/routes/${r.id_route}/live`}
                        className="mono"
                        style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)' }}
                      >
                        {r.truck_plate_number ?? '—'} · {r.driver_full_name ?? '—'}
                      </Link>
                      <StatusPill badge={incidentState[r.incident_state]} />
                    </div>
                    <span style={{ fontSize: 11.5, color: 'var(--text-soft)' }}>
                      {r.origin_name} → {r.destination_name}
                    </span>
                    <span
                      className="mono"
                      style={{
                        fontSize: 11,
                        color: pos && pos.kind === 'live' ? 'var(--text-faint)' : 'var(--warn)',
                      }}
                    >
                      {pos ? positionSummary(pos) : 'No position yet'}
                    </span>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 2 }}>
                      <Link
                        to={`/routes/${r.id_route}/live`}
                        className="btn btn--ghost"
                        style={{ padding: '4px 10px', fontSize: 11.5 }}
                      >
                        View live
                      </Link>
                      <RouteEndActions
                        route={r}
                        role={role}
                        token={token}
                        onChanged={() => void refresh()}
                        compact
                      />
                    </div>
                  </div>
                )
              })}
              {live.routes.length === 0 && (
                <div style={{ fontSize: 12, color: 'var(--text-faint)', padding: 12, textAlign: 'center' }}>
                  No routes on the road.
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function MapLegend() {
  const items: { label: string; color: string; stale?: boolean }[] = [
    { label: 'Normal', color: toneColor.good },
    { label: 'Medium', color: toneColor.warn },
    { label: 'Critical', color: toneColor.bad },
    { label: 'Last known position', color: toneColor.neutral, stale: true },
  ]
  return (
    <div
      style={{
        position: 'absolute',
        left: 12,
        bottom: 12,
        zIndex: 1000,
        pointerEvents: 'none',
        display: 'flex',
        gap: 14,
        background: 'oklch(1 0 0 / 0.85)',
        padding: '7px 12px',
        borderRadius: 8,
        border: '1px solid var(--border-soft)',
      }}
    >
      {items.map((i) => (
        <span
          key={i.label}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 5,
            fontSize: 11,
            color: 'oklch(0.28 0.02 258)',
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              boxSizing: 'border-box',
              background: i.stale ? 'transparent' : i.color,
              border: i.stale ? `1.5px dashed ${i.color}` : 'none',
            }}
          />
          {i.label}
        </span>
      ))}
    </div>
  )
}

function Tile({
  label,
  value,
  color,
  border,
  bg,
}: {
  label: string
  value: number
  color?: string
  border?: string
  bg?: string
}) {
  return (
    <div className="stat-card" style={{ borderColor: border, background: bg }}>
      <div className="stat-card__label">{label}</div>
      <div className="stat-card__value" style={{ color }}>
        {value}
      </div>
    </div>
  )
}
