import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import PageHeader from '../components/PageHeader'
import SearchBox from '../components/SearchBox'
import FilterChips from '../components/FilterChips'
import Icon from '../components/Icon'
import { useAuth } from '../context/AuthContext'
import { listRoutes } from '../api/routes'
import { listAlerts } from '../api/alerts'
import { listDrivers } from '../api/drivers'
import { listTrucks } from '../api/trucks'
import { ApiError } from '../api/client'
import { clock, relativeTime, shortDate } from '../utils/format'
import { severity } from '../utils/status'
import type { Alert, AlertSeverity, Driver, Route, Truck } from '../types'

const POLL_MS = 7000

// No "Low" chip — see LiveOps.tsx's FILTERS comment: a real Alert row is never actually
// severity_level "low" in practice.
const FILTERS: { key: AlertSeverity | 'all'; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'critical', label: 'Critical' },
  { key: 'medium', label: 'Medium' },
]

/**
 * "Alert history" — every alert a human has already reviewed (`reviewed_by_operator`), fetched
 * directly via `GET /api/alerts?reviewed=true` rather than folded into `RouteHistory.tsx`
 * (which is keyed off route *completion*, an unrelated concept — a reviewed alert on a still
 * in-progress route wouldn't show up there at all). Reviewing an alert from `RouteLiveView` is
 * the entire mechanism that makes it appear here: no separate "move to history" step exists,
 * it's just this screen's `reviewed: true` filter picking up what `LiveOps.tsx`'s default
 * `Unreviewed` filter just started excluding.
 */
export default function AlertHistory() {
  const { session } = useAuth()
  const token = session!.token

  const [alerts, setAlerts] = useState<Alert[]>([])
  const [routes, setRoutes] = useState<Route[]>([])
  const [drivers, setDrivers] = useState<Driver[]>([])
  const [trucks, setTrucks] = useState<Truck[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [severityFilter, setSeverityFilter] = useState<AlertSeverity | 'all'>('all')

  // Routes/drivers/trucks resolve names for each alert's row — fetched once, not polled.
  useEffect(() => {
    listRoutes(token).then(setRoutes).catch(() => {})
    listDrivers(token).then(setDrivers).catch(() => {})
    listTrucks(token).then(setTrucks).catch(() => {})
  }, [token])

  useEffect(() => {
    let cancelled = false
    const poll = () => {
      listAlerts(token, { reviewed: true })
        .then((rows) => {
          if (cancelled) return
          setAlerts(rows)
          setError(null)
        })
        .catch((err) => {
          if (!cancelled) {
            setError(err instanceof ApiError ? err.message : 'Failed to load alert history')
          }
        })
        .finally(() => {
          if (!cancelled) setLoading(false)
        })
    }
    poll()
    const id = setInterval(poll, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [token])

  const routeById = useMemo(() => new Map(routes.map((r) => [r.id_route, r])), [routes])
  const driverById = useMemo(() => new Map(drivers.map((d) => [d.id_driver, d])), [drivers])
  const truckById = useMemo(() => new Map(trucks.map((t) => [t.id_truck, t])), [trucks])

  const rows = useMemo(() => {
    return [...alerts]
      .sort((a, b) => +new Date(b.timestamp) - +new Date(a.timestamp))
      .map((a) => {
        const route = routeById.get(a.id_route)
        const driver = route ? driverById.get(route.id_driver) : undefined
        const truck = route ? truckById.get(route.id_truck) : undefined
        return {
          alert: a,
          originDestination: route ? `${route.origin_name} → ${route.destination_name}` : null,
          plate: truck?.plate_number ?? null,
          driverName: driver ? `${driver.first_name} ${driver.last_name}` : null,
        }
      })
  }, [alerts, routeById, driverById, truckById])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return rows
      .filter((r) => severityFilter === 'all' || r.alert.severity_level === severityFilter)
      .filter((r) => {
        if (!q) return true
        return `${r.alert.alert_type} ${r.originDestination ?? ''} ${r.driverName ?? ''} ${
          r.plate ?? ''
        }`
          .toLowerCase()
          .includes(q)
      })
  }, [rows, query, severityFilter])

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        padding: '24px 28px',
        gap: 18,
        height: '100%',
        overflow: 'hidden',
      }}
    >
      <PageHeader
        title="Alert history"
        subtitle={`${alerts.length} reviewed alerts`}
        actions={
          <SearchBox
            value={query}
            onChange={setQuery}
            placeholder="Search route, driver or truck…"
          />
        }
      />

      <FilterChips options={FILTERS} value={severityFilter} onChange={setSeverityFilter} />

      {error && (
        <div role="alert" style={{ color: 'var(--danger, #e5484d)', fontSize: 13 }}>
          {error}
        </div>
      )}

      <div className="panel" style={{ flex: 1, overflow: 'auto', padding: 6 }}>
        {loading ? (
          <div style={{ padding: 28, textAlign: 'center', color: 'var(--text-faint)' }}>
            Loading alert history…
          </div>
        ) : filtered.length === 0 ? (
          <div style={{ padding: 28, textAlign: 'center', color: 'var(--text-faint)' }}>
            No reviewed alerts match these filters.
          </div>
        ) : (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
            {filtered.map(({ alert, originDestination, plate, driverName }) => {
              const sev = severity[alert.severity_level]
              return (
                <li key={alert.id_alert}>
                  <Link
                    to={`/alerts/${alert.id_alert}`}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 12,
                      padding: '10px 12px',
                      borderRadius: 8,
                      textDecoration: 'none',
                      color: 'inherit',
                    }}
                  >
                    <Icon
                      name="check"
                      size={14}
                      style={{ color: 'var(--good)', flexShrink: 0 }}
                    />
                    <span style={{ fontSize: 12.5, fontWeight: 500, minWidth: 160 }}>
                      {alert.alert_type}
                    </span>
                    <span
                      className={sev.tone === 'neutral' ? 'pill' : `pill pill--${sev.tone}`}
                      style={{ fontSize: 10, padding: '2px 8px' }}
                    >
                      {sev.label}
                    </span>
                    <span className="mono" style={{ fontSize: 11.5, color: 'var(--text-soft)', minWidth: 150 }}>
                      {plate ?? '—'} · {driverName ?? '—'}
                    </span>
                    <span style={{ fontSize: 11.5, color: 'var(--text-faint)', minWidth: 170 }}>
                      {originDestination ?? '—'}
                    </span>
                    <span
                      className="mono"
                      style={{
                        fontSize: 11,
                        color: 'var(--text-faint)',
                        marginLeft: 'auto',
                      }}
                    >
                      {shortDate(alert.timestamp)}, {clock(alert.timestamp)} ·{' '}
                      {relativeTime(alert.timestamp)}
                    </span>
                  </Link>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </div>
  )
}
