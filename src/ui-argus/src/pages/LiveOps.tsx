import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import PageHeader from '../components/PageHeader'
import FleetMap, { type FleetMapMarker } from '../components/FleetMap'
import { useAuth } from '../context/AuthContext'
import { listActiveRoutes } from '../api/routes'
import { listAlerts } from '../api/alerts'
import { listDrivers } from '../api/drivers'
import { listTrucks } from '../api/trucks'
import { ApiError } from '../api/client'
import { longDay, relativeTime } from '../utils/format'
import { severity } from '../utils/status'
import { toLatLng } from '../utils/geo'
import type { AlertSeverity, Driver, RouteWithStatus, Truck } from '../types'

/**
 * Live operations dashboard against the real API. `GET /api/routes/active` (built specifically
 * for this screen — see `src/backend-argus/CLAUDE.md`) feeds the map/stat tiles;
 * `GET /api/alerts` feeds the feed. Polling on an interval is the interim real-time strategy
 * `INTEGRATION.md` gap #5 names as acceptable pending a real push mechanism (WebSocket/SSE).
 * Drivers/trucks are fetched once (not polled) purely to resolve names for alerts whose route
 * isn't currently active — active routes already embed their own names.
 */

const POLL_MS = 7000

const toneColor = {
  good: 'var(--good)',
  warn: 'var(--warn)',
  bad: 'var(--bad)',
  neutral: 'var(--text-faint)',
} as const

const FILTERS: { key: AlertSeverity | 'all'; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'critical', label: 'Critical' },
  { key: 'medium', label: 'Medium' },
  { key: 'low', label: 'Low' },
]

export default function LiveOps() {
  const { session } = useAuth()
  const token = session!.token

  const [filter, setFilter] = useState<AlertSeverity | 'all'>('all')
  const [activeRoutes, setActiveRoutes] = useState<RouteWithStatus[]>([])
  const [alerts, setAlerts] = useState<Awaited<ReturnType<typeof listAlerts>>>([])
  const [drivers, setDrivers] = useState<Driver[]>([])
  const [trucks, setTrucks] = useState<Truck[]>([])
  const [error, setError] = useState<string | null>(null)

  // Drivers/trucks resolve names for alerts on routes that are no longer active — fetched once.
  useEffect(() => {
    listDrivers(token).then(setDrivers).catch(() => {})
    listTrucks(token).then(setTrucks).catch(() => {})
  }, [token])

  useEffect(() => {
    let cancelled = false
    const poll = () => {
      Promise.all([listActiveRoutes(token), listAlerts(token)])
        .then(([routes, alertRows]) => {
          if (cancelled) return
          setActiveRoutes(routes)
          setAlerts(alertRows)
          setError(null)
        })
        .catch((err) => {
          if (!cancelled) setError(err instanceof ApiError ? err.message : 'Failed to load live data')
        })
    }
    poll()
    const id = setInterval(poll, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [token])

  const routeById = useMemo(
    () => new Map(activeRoutes.map((r) => [r.id_route, r])),
    [activeRoutes],
  )
  const driverById = useMemo(() => new Map(drivers.map((d) => [d.id_driver, d])), [drivers])
  const truckById = useMemo(() => new Map(trucks.map((t) => [t.id_truck, t])), [trucks])

  const tiles = useMemo(() => {
    const counts = { low: 0, medium: 0, critical: 0 }
    for (const r of activeRoutes) {
      if (r.latest_status) counts[r.latest_status.vigilance]++
    }
    return {
      onRoute: activeRoutes.length,
      low: counts.low,
      medium: counts.medium,
      critical: counts.critical,
    }
  }, [activeRoutes])

  const markers = useMemo<FleetMapMarker[]>(
    () =>
      activeRoutes
        .filter((r) => r.latest_status)
        .map((r) => {
          const s = r.latest_status!
          const v = severity[s.vigilance]
          return {
            id: s.id_status_route,
            position: toLatLng(s.current_coordinates),
            plate: r.truck_plate_number ?? '—',
            driver: r.driver_full_name ?? '—',
            origin: r.origin_name,
            destination: r.destination_name,
            speedKmh: s.current_speed,
            vigilanceLabel: v.label,
            tone: v.tone,
            timestamp: s.timestamp,
          }
        }),
    [activeRoutes],
  )

  const feed = useMemo(() => {
    const rows = [...alerts].sort(
      (a, b) => +new Date(b.timestamp) - +new Date(a.timestamp),
    )
    return (filter === 'all' ? rows : rows.filter((a) => a.severity_level === filter)).map(
      (a) => {
        const route = routeById.get(a.id_route)
        const driver = route ? driverById.get(route.id_driver) : undefined
        const truck = route ? truckById.get(route.id_truck) : undefined
        return {
          alert: a,
          originDestination: route ? `${route.origin_name} → ${route.destination_name}` : null,
          plate: route?.truck_plate_number ?? truck?.plate_number ?? null,
          driverName: route?.driver_full_name ?? (driver ? `${driver.first_name} ${driver.last_name}` : null),
        }
      },
    )
  }, [alerts, filter, routeById, driverById, truckById])

  const todayCount = alerts.length

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

      {/* stat tiles */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
          gap: 14,
        }}
      >
        <Tile label="Trucks on route" value={tiles.onRoute} />
        <Tile label="Low severity" value={tiles.low} color="var(--good)" />
        <Tile
          label="Medium severity"
          value={tiles.medium}
          color="var(--warn)"
          border="var(--warn)"
        />
        <Tile
          label="Critical severity"
          value={tiles.critical}
          color="var(--bad)"
          border="var(--bad)"
          bg="var(--bad-soft)"
        />
      </div>

      {/* body */}
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
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
            }}
          >
            <span style={{ fontSize: 13, fontWeight: 600 }}>
              Fleet map — Central Mexico &amp; Bajío
            </span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: '50%',
                  background: 'var(--bad)',
                }}
              />
              <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>
                LIVE
              </span>
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
            <FleetMap markers={markers} />

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
              {(['good', 'warn', 'bad'] as const).map((t, i) => (
                <span
                  key={t}
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
                      background: toneColor[t],
                    }}
                  />
                  {['Low', 'Medium', 'Critical'][i]}
                </span>
              ))}
            </div>
          </div>
        </div>

        {/* alert feed */}
        <div
          className="panel"
          style={{
            width: 380,
            flexShrink: 0,
            padding: 16,
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
            }}
          >
            <span style={{ fontSize: 13, fontWeight: 600 }}>Recent alerts</span>
            <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>
              {todayCount} today
            </span>
          </div>

          <div style={{ display: 'flex', gap: 4 }}>
            {FILTERS.map((f) => (
              <button
                key={f.key}
                onClick={() => setFilter(f.key)}
                style={{
                  fontSize: 11,
                  padding: '4px 10px',
                  borderRadius: 20,
                  border: 'none',
                  cursor: 'pointer',
                  font: 'inherit',
                  background:
                    filter === f.key ? 'var(--surface-3)' : 'transparent',
                  color:
                    filter === f.key ? 'var(--text)' : 'var(--text-faint)',
                }}
              >
                {f.label}
              </button>
            ))}
          </div>

          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
              overflowY: 'auto',
            }}
          >
            {feed.map(({ alert, originDestination, plate, driverName }) => {
              const sev = severity[alert.severity_level]
              const resolved = alert.reviewed_by_operator
              return (
                <Link
                  key={alert.id_alert}
                  to={`/alerts/${alert.id_alert}`}
                  style={{
                    display: 'flex',
                    gap: 10,
                    padding: 11,
                    borderRadius: 9,
                    color: 'inherit',
                    background:
                      sev.tone === 'bad' && !resolved
                        ? 'var(--bad-soft)'
                        : 'var(--surface-2)',
                    border: `1px solid ${
                      sev.tone === 'bad' && !resolved
                        ? 'var(--bad)'
                        : 'var(--border-soft)'
                    }`,
                    opacity: resolved ? 0.6 : 1,
                  }}
                >
                  <span
                    style={{
                      width: 3,
                      borderRadius: 2,
                      flexShrink: 0,
                      background: resolved
                        ? 'var(--good)'
                        : toneColor[sev.tone],
                    }}
                  />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        gap: 8,
                      }}
                    >
                      <span style={{ fontSize: 12.5, fontWeight: 600 }}>
                        {alert.alert_type}
                      </span>
                      <span
                        style={{
                          fontSize: 10.5,
                          fontWeight: 600,
                          color: resolved
                            ? 'var(--text-faint)'
                            : toneColor[sev.tone],
                        }}
                      >
                        {resolved ? 'RESOLVED' : sev.label.toUpperCase()}
                      </span>
                    </div>
                    <div
                      className="mono"
                      style={{
                        fontSize: 11,
                        color: 'var(--text-soft)',
                        marginTop: 3,
                      }}
                    >
                      {plate ?? '—'} · {driverName ?? '—'}
                    </div>
                    <div
                      style={{
                        fontSize: 11,
                        color: 'var(--text-faint)',
                        marginTop: 1,
                      }}
                    >
                      {originDestination ?? '—'} · {relativeTime(alert.timestamp)}
                    </div>
                  </div>
                </Link>
              )
            })}
            {feed.length === 0 && (
              <div
                style={{
                  fontSize: 12,
                  color: 'var(--text-faint)',
                  padding: 12,
                  textAlign: 'center',
                }}
              >
                No alerts at this severity.
              </div>
            )}
          </div>
        </div>
      </div>
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
