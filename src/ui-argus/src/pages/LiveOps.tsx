import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import PageHeader from '../components/PageHeader'
import {
  alerts as allAlerts,
  driverById,
  routeById,
  routes,
  statusRoutes,
  truckById,
} from '../data/fixtures'
import { longDay, relativeTime } from '../utils/format'
import { isActiveRoute, severity, vigilance } from '../utils/status'
import type { AlertSeverity, Coordinates } from '../types'

/** Linear lat/lon → SVG projection over a central-Mexico bounding box. */
const BOX = { lonMin: -104, lonMax: -98, latMin: 18.7, latMax: 21.7 }
const VIEW = { w: 760, h: 460 }
function project({ lat, lon }: Coordinates) {
  return {
    x: ((lon - BOX.lonMin) / (BOX.lonMax - BOX.lonMin)) * VIEW.w,
    y: ((BOX.latMax - lat) / (BOX.latMax - BOX.latMin)) * VIEW.h,
  }
}

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
  const [filter, setFilter] = useState<AlertSeverity | 'all'>('all')

  const tiles = useMemo(() => {
    const active = routes.filter((r) => isActiveRoute(r.operative_status))
    const counts = { normal: 0, low_vigilance: 0, critical: 0 }
    for (const s of statusRoutes) counts[s.vigilance]++
    return {
      onRoute: active.length,
      normal: counts.normal,
      low: counts.low_vigilance,
      critical: counts.critical,
    }
  }, [])

  const markers = useMemo(
    () =>
      statusRoutes.map((s) => {
        const route = routeById(s.id_route)
        const truck = route && truckById(route.id_truck)
        return {
          id: s.id_status_route,
          plate: truck?.plate_number ?? '—',
          tone: vigilance[s.vigilance].tone,
          ...project(s.current_coordinates),
        }
      }),
    [],
  )

  const feed = useMemo(() => {
    const rows = [...allAlerts].sort(
      (a, b) => +new Date(b.timestamp) - +new Date(a.timestamp),
    )
    return (filter === 'all' ? rows : rows.filter((a) => a.severity_level === filter)).map(
      (a) => {
        const route = routeById(a.id_route)
        const driver = route && driverById(route.id_driver)
        const truck = route && truckById(route.id_truck)
        return { alert: a, route, driver, truck }
      },
    )
  }, [filter])

  const todayCount = allAlerts.length

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
                background: 'var(--good)',
                boxShadow: '0 0 0 3px var(--good-soft)',
              }}
            />
            <span style={{ fontSize: 12, color: 'var(--text-soft)' }}>
              Fleet connection stable
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
        <Tile label="Normal status" value={tiles.normal} color="var(--good)" />
        <Tile
          label="Low vigilance"
          value={tiles.low}
          color="var(--warn)"
          border="var(--warn)"
        />
        <Tile
          label="Drowsiness — critical"
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
              position: 'relative',
              borderRadius: 8,
              overflow: 'hidden',
              background: `
                linear-gradient(var(--border-soft) 1px, transparent 1px) 0 0/40px 40px,
                linear-gradient(90deg, var(--border-soft) 1px, transparent 1px) 0 0/40px 40px,
                oklch(0.165 0.018 258)`,
            }}
          >
            <svg
              width="100%"
              height="100%"
              viewBox={`0 0 ${VIEW.w} ${VIEW.h}`}
              preserveAspectRatio="none"
              style={{ position: 'absolute', inset: 0 }}
            >
              <path
                d="M40 380 C 160 340, 220 200, 340 190 S 520 120, 620 60"
                stroke="oklch(0.4 0.02 258)"
                strokeWidth={10}
                fill="none"
                strokeLinecap="round"
              />
              <path
                d="M40 380 C 160 340, 220 200, 340 190 S 520 120, 620 60"
                stroke="var(--accent)"
                strokeWidth={2}
                fill="none"
                strokeDasharray="3 7"
                opacity={0.7}
              />
              <path
                d="M120 420 C 260 430, 380 340, 420 260"
                stroke="oklch(0.4 0.02 258)"
                strokeWidth={8}
                fill="none"
                strokeLinecap="round"
              />
            </svg>

            {markers.map((m) => (
              <div
                key={m.id}
                style={{
                  position: 'absolute',
                  left: `${(m.x / VIEW.w) * 100}%`,
                  top: `${(m.y / VIEW.h) * 100}%`,
                  transform: 'translate(-50%, -50%)',
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  gap: 3,
                }}
              >
                <span
                  style={{
                    width: m.tone === 'bad' ? 14 : 12,
                    height: m.tone === 'bad' ? 14 : 12,
                    borderRadius: '50%',
                    background: toneColor[m.tone],
                    border: '2px solid oklch(0.95 0.01 258 / 0.85)',
                    boxShadow:
                      m.tone === 'bad' ? '0 0 0 6px var(--bad-soft)' : 'none',
                  }}
                />
                <span
                  className="mono"
                  style={{
                    fontSize: 10,
                    background:
                      m.tone === 'bad' ? 'var(--bad)' : 'var(--surface-3)',
                    color:
                      m.tone === 'bad' ? 'oklch(0.16 0.02 258)' : 'var(--text)',
                    fontWeight: m.tone === 'bad' ? 600 : 400,
                    padding: '1px 5px',
                    borderRadius: 4,
                  }}
                >
                  {m.plate}
                </span>
              </div>
            ))}

            <div
              style={{
                position: 'absolute',
                left: 14,
                bottom: 12,
                display: 'flex',
                gap: 14,
                background: 'oklch(0.14 0.018 258 / 0.7)',
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
                    color: 'var(--text-soft)',
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
                  {['Normal', 'Low vigilance', 'Critical'][i]}
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
            {feed.map(({ alert, route, driver, truck }) => {
              const sev = severity[alert.severity_level]
              const resolved = alert.reviwed_by_operator
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
                      {truck?.plate_number ?? '—'} ·{' '}
                      {driver
                        ? `${driver.first_name} ${driver.last_name}`
                        : '—'}
                    </div>
                    <div
                      style={{
                        fontSize: 11,
                        color: 'var(--text-faint)',
                        marginTop: 1,
                      }}
                    >
                      {route
                        ? `${route.origin_name} → ${route.destination_name}`
                        : '—'}{' '}
                      · {relativeTime(alert.timestamp)}
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
