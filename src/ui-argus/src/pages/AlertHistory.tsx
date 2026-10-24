import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import PageHeader from '../components/PageHeader'
import SearchBox from '../components/SearchBox'
import FilterChips from '../components/FilterChips'
import { IncidentCard } from '../components/IncidentFeed'
import { useAuth } from '../context/AuthContext'
import { usePolling } from '../hooks/usePolling'
import { listRoutes } from '../api/routes'
import { listIncidents } from '../api/incidents'
import { ApiError } from '../api/client'
import { alertTypeLabel } from '../utils/status'
import { ALERT_HISTORY_POLL_MS } from '../constants'
import type { Incident, Route } from '../types'

/** How many closed incidents the screen reads (the backend allows up to 500). */
const HISTORY_LIMIT = 200

const FILTERS: { key: Incident['severity'] | 'all'; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'critical', label: 'Critical' },
  { key: 'medium', label: 'Medium' },
]

/**
 * "Alert history": the **closed** incidents (`GET /api/incidents?status=closed`), newest close
 * first, polled every 5 s (paused while the tab is hidden). An incident leaves Live operations'
 * feed and lands here the moment a guardian or root_admin closes it — closing is per incident,
 * with a note (required for a critical one), and it closes every alert of the incident. Each
 * incident shows its close time and note and all of its alerts, each with its position
 * ("Last known (N old)" / "Position unknown" where that applies) and the replayed badge.
 *
 * This replaced the earlier per-alert `reviewed=true` list: `reviewed_by_operator` is still set
 * on every alert of a closed incident, but "closed" is now the one state that moves an alert
 * out of the live feed.
 */
export default function AlertHistory() {
  const { session } = useAuth()
  const token = session!.token

  const [incidents, setIncidents] = useState<Incident[]>([])
  const [routes, setRoutes] = useState<Route[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [severityFilter, setSeverityFilter] = useState<Incident['severity'] | 'all'>('all')

  // Routes resolve origin → destination for each incident's header — fetched once, not polled.
  useEffect(() => {
    listRoutes(token).then(setRoutes).catch(() => {})
  }, [token])

  const poll = useCallback(
    () =>
      listIncidents(token, { status: 'closed', limit: HISTORY_LIMIT })
        .then((rows) => {
          setIncidents(rows)
          setError(null)
        })
        .catch((err) => {
          setError(err instanceof ApiError ? err.message : 'Failed to load alert history')
        })
        .finally(() => setLoading(false)),
    [token],
  )
  usePolling(poll, ALERT_HISTORY_POLL_MS, { resetKey: token })

  const routeById = useMemo(() => new Map(routes.map((r) => [r.id_route, r])), [routes])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return [...incidents]
      .sort((a, b) => Date.parse(b.closed_at ?? '') - Date.parse(a.closed_at ?? ''))
      .filter((i) => severityFilter === 'all' || i.severity === severityFilter)
      .filter((i) => {
        if (!q) return true
        const route = routeById.get(i.id_route)
        return [
          i.truck_plate_number ?? '',
          i.driver_full_name ?? '',
          i.close_note,
          route ? `${route.origin_name} ${route.destination_name}` : '',
          ...i.alerts.map((a) => alertTypeLabel(a.alert_type)),
        ]
          .join(' ')
          .toLowerCase()
          .includes(q)
      })
  }, [incidents, query, severityFilter, routeById])

  const alertCount = incidents.reduce((n, i) => n + i.alert_count, 0)

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
        subtitle={`${incidents.length} closed incidents · ${alertCount} alerts`}
        actions={
          <SearchBox
            value={query}
            onChange={setQuery}
            placeholder="Search truck, driver, route, alert or note…"
          />
        }
      />

      <FilterChips options={FILTERS} value={severityFilter} onChange={setSeverityFilter} />

      {error && (
        <div role="alert" style={{ color: 'var(--danger, #e5484d)', fontSize: 13 }}>
          {error}
        </div>
      )}

      <div className="panel" style={{ flex: 1, overflow: 'auto', padding: 10 }}>
        {loading ? (
          <div style={{ padding: 28, textAlign: 'center', color: 'var(--text-faint)' }}>
            Loading alert history…
          </div>
        ) : filtered.length === 0 ? (
          <div style={{ padding: 28, textAlign: 'center', color: 'var(--text-faint)' }}>
            No closed incidents match these filters.
          </div>
        ) : (
          <ul
            style={{
              listStyle: 'none',
              margin: 0,
              padding: 0,
              display: 'flex',
              flexDirection: 'column',
              gap: 12,
            }}
          >
            {filtered.map((inc) => {
              const route = routeById.get(inc.id_route)
              return (
                <li key={inc.id_incident} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                    <Link
                      to={`/routes/${inc.id_route}/live`}
                      className="mono"
                      style={{ fontSize: 12, color: 'var(--text-soft)' }}
                    >
                      {inc.truck_plate_number ?? '—'} · {inc.driver_full_name ?? '—'}
                    </Link>
                    <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
                      {route ? `${route.origin_name} → ${route.destination_name}` : '—'}
                    </span>
                  </div>
                  <IncidentCard incident={inc} canClose={false} />
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </div>
  )
}
