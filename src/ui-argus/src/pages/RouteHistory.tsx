import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import PageHeader from '../components/PageHeader'
import SearchBox from '../components/SearchBox'
import StatusPill from '../components/StatusPill'
import Icon from '../components/Icon'
import { useAuth } from '../context/AuthContext'
import { listRoutes } from '../api/routes'
import { listAlerts } from '../api/alerts'
import { listDrivers } from '../api/drivers'
import { listTrucks } from '../api/trucks'
import { ApiError } from '../api/client'
import { clock, relativeTime, shortDate } from '../utils/format'
import { routeStatus, severity } from '../utils/status'
import type { Alert, Driver, Route, Truck } from '../types'

/**
 * "Monitoring — Route history": every completed trip against `GET /api/routes?status=completed`,
 * each expandable into the alerts raised during it (`GET /api/alerts`, grouped client-side by
 * `id_route` — both endpoints already return everything this screen needs, so no new backend
 * route was added for it). This is the real screen behind Sidebar.tsx's "Route history" item,
 * previously a `soon` placeholder labeled "Trip history". Routes and alerts are both readable by
 * every role per the backend's RBAC table, so — like Live operations — this screen carries no
 * `roles` restriction in Sidebar.tsx.
 */
export default function RouteHistory() {
  const { session } = useAuth()
  const token = session!.token

  const [routes, setRoutes] = useState<Route[]>([])
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [drivers, setDrivers] = useState<Driver[]>([])
  const [trucks, setTrucks] = useState<Truck[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    Promise.all([
      listRoutes(token, 'completed'),
      listAlerts(token),
      listDrivers(token),
      listTrucks(token),
    ])
      .then(([routesList, alertsList, driversList, trucksList]) => {
        if (cancelled) return
        setRoutes(routesList)
        setAlerts(alertsList)
        setDrivers(driversList)
        setTrucks(trucksList)
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : 'Failed to load route history')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [token])

  const driverById = useMemo(() => new Map(drivers.map((d) => [d.id_driver, d])), [drivers])
  const truckById = useMemo(() => new Map(trucks.map((t) => [t.id_truck, t])), [trucks])
  const driverName = (id: string) => {
    const d = driverById.get(id)
    return d ? `${d.first_name} ${d.last_name}` : '—'
  }

  // `GET /api/alerts` already sorts newest-first (see `app/routers/alerts.py`'s `list_alerts`);
  // grouping preserves that order within each route's bucket.
  const alertsByRoute = useMemo(() => {
    const map = new Map<string, Alert[]>()
    for (const a of alerts) {
      const list = map.get(a.id_route)
      if (list) list.push(a)
      else map.set(a.id_route, [a])
    }
    return map
  }, [alerts])

  const sorted = useMemo(
    () =>
      [...routes].sort((a, b) => {
        const ta = a.actual_arrival ?? a.estimated_arrival ?? a.estimated_departure
        const tb = b.actual_arrival ?? b.estimated_arrival ?? b.estimated_departure
        return new Date(tb).getTime() - new Date(ta).getTime()
      }),
    [routes],
  )

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return sorted
    return sorted.filter((r) => {
      const truck = truckById.get(r.id_truck)
      return `${r.origin_name} ${r.destination_name} ${driverName(r.id_driver)} ${
        truck?.plate_number ?? ''
      }`
        .toLowerCase()
        .includes(q)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- driverName closes over driverById
  }, [sorted, query, truckById, driverById])

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const totalAlerts = routes.reduce(
    (n, r) => n + (alertsByRoute.get(r.id_route)?.length ?? 0),
    0,
  )

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
        title="Route history"
        subtitle={`${routes.length} completed trips · ${totalAlerts} alerts recorded`}
        actions={
          <SearchBox
            value={query}
            onChange={setQuery}
            placeholder="Search route, driver or truck…"
          />
        }
      />

      {error && (
        <div role="alert" style={{ color: 'var(--danger, #e5484d)', fontSize: 13 }}>
          {error}
        </div>
      )}

      <div className="panel" style={{ flex: 1, overflow: 'auto', padding: 6 }}>
        {loading ? (
          <div style={{ padding: 28, textAlign: 'center', color: 'var(--text-faint)' }}>
            Loading route history…
          </div>
        ) : filtered.length === 0 ? (
          <div style={{ padding: 28, textAlign: 'center', color: 'var(--text-faint)' }}>
            No completed routes yet.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {filtered.map((r) => {
              const routeAlerts = alertsByRoute.get(r.id_route) ?? []
              const isOpen = expanded.has(r.id_route)
              const truck = truckById.get(r.id_truck)
              return (
                <div
                  key={r.id_route}
                  style={{
                    border: '1px solid var(--border-soft)',
                    borderRadius: 10,
                    overflow: 'hidden',
                  }}
                >
                  <button
                    onClick={() => toggle(r.id_route)}
                    aria-expanded={isOpen}
                    style={{
                      width: '100%',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 14,
                      padding: '12px 14px',
                      background: 'transparent',
                      border: 'none',
                      cursor: 'pointer',
                      textAlign: 'left',
                      font: 'inherit',
                      color: 'var(--text)',
                    }}
                  >
                    <Icon
                      name="chevron-down"
                      size={14}
                      style={{
                        color: 'var(--text-faint)',
                        transform: isOpen ? 'rotate(0deg)' : 'rotate(-90deg)',
                        transition: 'transform .12s',
                        flexShrink: 0,
                      }}
                    />
                    <span style={{ fontWeight: 600, fontSize: 13.5, minWidth: 220 }}>
                      {r.origin_name} → {r.destination_name}
                    </span>
                    <span style={{ fontSize: 12.5, color: 'var(--text-soft)', minWidth: 150 }}>
                      {driverName(r.id_driver)}
                    </span>
                    <span
                      className="mono"
                      style={{ fontSize: 12, color: 'var(--text-faint)', minWidth: 90 }}
                    >
                      {truck?.plate_number ?? '—'}
                    </span>
                    <span className="mono" style={{ fontSize: 12, color: 'var(--text-faint)' }}>
                      {clock(r.actual_departure ?? r.estimated_departure)} →{' '}
                      {clock(r.actual_arrival ?? r.estimated_arrival)}
                    </span>
                    <span
                      style={{
                        marginLeft: 'auto',
                        display: 'flex',
                        alignItems: 'center',
                        gap: 10,
                      }}
                    >
                      <StatusPill badge={routeStatus[r.operative_status]} />
                      <span
                        className={routeAlerts.length ? 'pill pill--warn' : 'pill'}
                        style={{ fontSize: 10.5, padding: '3px 9px' }}
                      >
                        {routeAlerts.length} alert{routeAlerts.length === 1 ? '' : 's'}
                      </span>
                    </span>
                  </button>

                  {isOpen && (
                    <div
                      style={{
                        borderTop: '1px solid var(--border-soft)',
                        background: 'var(--surface-2)',
                        padding: routeAlerts.length
                          ? '4px 14px 10px 42px'
                          : '14px 14px 14px 42px',
                      }}
                    >
                      {routeAlerts.length === 0 ? (
                        <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>
                          No alerts recorded for this trip.
                        </span>
                      ) : (
                        <ul
                          style={{
                            listStyle: 'none',
                            margin: 0,
                            padding: 0,
                            display: 'flex',
                            flexDirection: 'column',
                            gap: 4,
                          }}
                        >
                          {routeAlerts.map((a) => {
                            const sev = severity[a.severity_level]
                            return (
                              <li key={a.id_alert}>
                                <Link
                                  to={`/alerts/${a.id_alert}`}
                                  style={{
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: 12,
                                    padding: '8px 10px',
                                    borderRadius: 8,
                                    textDecoration: 'none',
                                    color: 'inherit',
                                  }}
                                >
                                  <Icon
                                    name="alert-triangle"
                                    size={14}
                                    style={{ color: 'var(--text-faint)', flexShrink: 0 }}
                                  />
                                  <span
                                    style={{ fontSize: 12.5, fontWeight: 500, minWidth: 160 }}
                                  >
                                    {a.alert_type}
                                  </span>
                                  <span
                                    className={
                                      sev.tone === 'neutral' ? 'pill' : `pill pill--${sev.tone}`
                                    }
                                    style={{ fontSize: 10, padding: '2px 8px' }}
                                  >
                                    {sev.label}
                                  </span>
                                  <span
                                    className="mono"
                                    style={{
                                      fontSize: 11.5,
                                      color: 'var(--text-faint)',
                                      minWidth: 190,
                                    }}
                                  >
                                    {shortDate(a.timestamp)}, {clock(a.timestamp)} ·{' '}
                                    {relativeTime(a.timestamp)}
                                  </span>
                                  <span
                                    className="mono"
                                    style={{
                                      fontSize: 11,
                                      color: 'var(--text-faint)',
                                      marginLeft: 'auto',
                                    }}
                                  >
                                    lat {a.coordinates.lat.toFixed(4)}, lon{' '}
                                    {a.coordinates.lon.toFixed(4)}
                                  </span>
                                </Link>
                              </li>
                            )
                          })}
                        </ul>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
