import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import Icon from '../components/Icon'
import FilterChips from '../components/FilterChips'
import { useAuth } from '../context/AuthContext'
import { getAlert, listAlerts, reviewAlert, type AlertFilters } from '../api/alerts'
import { getRoute, getRouteStatus } from '../api/routes'
import { listDrivers } from '../api/drivers'
import { listTrucks } from '../api/trucks'
import { ApiError } from '../api/client'
import { clock, pct, relativeTime, shortDate } from '../utils/format'
import { severity } from '../utils/status'
import { toLatLng } from '../utils/geo'
import FleetMap, { type FleetMapMarker } from '../components/FleetMap'
import type {
  Alert,
  AlertSeverity,
  AlertSource,
  Driver,
  GripStatus,
  Route,
  StatusRoute,
  Truck,
} from '../types'

const SOURCE_LABEL: Record<AlertSource, string> = {
  fusion: 'Camera + grip sensor (fused)',
  panic_button: 'Driver-activated panic button',
}

const GRIP_LABEL: Record<GripStatus, string> = {
  good: 'Good',
  bad: 'Bad',
}

const POLL_MS = 7000

const toneColor = {
  good: 'var(--good)',
  warn: 'var(--warn)',
  bad: 'var(--bad)',
  neutral: 'var(--text-faint)',
} as const

const SEVERITY_RANK: Record<AlertSeverity, number> = { low: 0, medium: 1, critical: 2 }

const SEVERITY_FILTERS: { key: AlertSeverity | 'all'; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'critical', label: 'Critical' },
  { key: 'medium', label: 'Medium' },
  { key: 'low', label: 'Low' },
]

type ReviewedFilter = 'unreviewed' | 'reviewed' | 'all'

const REVIEWED_FILTERS: { key: ReviewedFilter; label: string }[] = [
  { key: 'unreviewed', label: 'Unreviewed' },
  { key: 'reviewed', label: 'Reviewed' },
  { key: 'all', label: 'All' },
]

type Trend = 'improving' | 'worsening' | 'steady' | 'unknown'

const TREND_LABEL: Record<Trend, string> = {
  improving: 'Improving',
  worsening: 'Worsening',
  steady: 'Steady',
  unknown: 'Trend: gathering data…',
}

const TREND_TONE: Record<Trend, 'good' | 'bad' | 'neutral'> = {
  improving: 'good',
  worsening: 'bad',
  steady: 'neutral',
  unknown: 'neutral',
}

/**
 * "Route live view" — the unified live-tracking + alert-triage page. Mounted at two URL shapes
 * (see `App.tsx`): the canonical `/routes/:routeId/live` (+ optional `?alert=` focus), and the
 * legacy `/alerts/:alertId` — kept working unchanged because `src/it-argus/tests/
 * live-ops.spec.ts` navigates to and asserts that exact URL shape in three places. On the legacy
 * path, the alert's `id_route` is resolved from the fetched alert itself before anything
 * route-scoped can be polled.
 *
 * Was `AlertTriage.tsx` (single-alert only) — generalized rather than duplicated, per the
 * project's "one unified page" decision: a second, parallel focus/tracking implementation
 * inside `LiveOps.tsx`'s own map would just be two divergent versions of the same idea.
 */
export default function RouteLiveView() {
  const { alertId: legacyAlertId, routeId: routeIdParam } = useParams<{
    alertId?: string
    routeId?: string
  }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const { session } = useAuth()
  const token = session!.token
  const canReview = session!.user.role === 'root_admin' || session!.user.role === 'guardian'

  const isLegacy = Boolean(legacyAlertId)
  const focusedAlertId = isLegacy ? legacyAlertId : (searchParams.get('alert') ?? undefined)

  // --- focused alert (optional) ---
  const [focusedAlert, setFocusedAlert] = useState<Alert | null>(null)
  const [alertLoading, setAlertLoading] = useState(Boolean(focusedAlertId))
  const [alertNotFound, setAlertNotFound] = useState(false)
  const [alertError, setAlertError] = useState<string | null>(null)

  const [reviewed, setReviewed] = useState(false)
  const [notes, setNotes] = useState('')
  const [saved, setSaved] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!focusedAlertId) {
      setFocusedAlert(null)
      setAlertLoading(false)
      setAlertNotFound(false)
      return
    }
    let cancelled = false
    setAlertLoading(true)
    setAlertNotFound(false)
    getAlert(focusedAlertId, token)
      .then((a) => {
        if (cancelled) return
        setFocusedAlert(a)
        setReviewed(a.reviewed_by_operator)
        setNotes(a.operator_notes)
        setSaved(false)
      })
      .catch((err) => {
        if (cancelled) return
        if (err instanceof ApiError && err.status === 404) {
          setAlertNotFound(true)
        } else {
          setAlertError(err instanceof ApiError ? err.message : 'Failed to load alert')
        }
      })
      .finally(() => {
        if (!cancelled) setAlertLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [focusedAlertId, token])

  // --- route + live status + this route's alerts ---
  const routeId = isLegacy ? (focusedAlert?.id_route ?? null) : (routeIdParam ?? null)

  const [route, setRoute] = useState<Route | null>(null)
  const [liveStatus, setLiveStatus] = useState<StatusRoute | null>(null)
  const [routeAlerts, setRouteAlerts] = useState<Alert[]>([])
  const [driver, setDriver] = useState<Driver | null>(null)
  const [truck, setTruck] = useState<Truck | null>(null)
  const [routeLoading, setRouteLoading] = useState(true)
  const [routeNotFound, setRouteNotFound] = useState(false)
  const [routeError, setRouteError] = useState<string | null>(null)
  const [trend, setTrend] = useState<Trend>('unknown')
  const prevVigilanceRef = useRef<AlertSeverity | null>(null)

  const [alertsSeverity, setAlertsSeverity] = useState<AlertSeverity | 'all'>('all')
  const [alertsReviewed, setAlertsReviewed] = useState<ReviewedFilter>('unreviewed')

  const [drivers, setDrivers] = useState<Driver[]>([])
  const [trucks, setTrucks] = useState<Truck[]>([])
  useEffect(() => {
    listDrivers(token).then(setDrivers).catch(() => {})
    listTrucks(token).then(setTrucks).catch(() => {})
  }, [token])

  useEffect(() => {
    if (!route) return
    setDriver(drivers.find((d) => d.id_driver === route.id_driver) ?? null)
    setTruck(trucks.find((t) => t.id_truck === route.id_truck) ?? null)
  }, [route, drivers, trucks])

  useEffect(() => {
    if (!routeId) return
    let cancelled = false
    const filters: AlertFilters = { route_id: routeId }
    if (alertsSeverity !== 'all') filters.severity = alertsSeverity
    if (alertsReviewed !== 'all') filters.reviewed = alertsReviewed === 'reviewed'

    const poll = () => {
      Promise.all([
        getRoute(routeId, token),
        getRouteStatus(routeId, token).catch((err) => {
          if (err instanceof ApiError && err.status === 404) return null
          throw err
        }),
        listAlerts(token, filters),
      ])
        .then(([r, status, alerts]) => {
          if (cancelled) return
          setRoute(r)
          setLiveStatus(status)
          setRouteAlerts(alerts)
          setRouteNotFound(false)
          setRouteError(null)
          if (status) {
            const prev = prevVigilanceRef.current
            setTrend(
              prev === null
                ? 'unknown'
                : SEVERITY_RANK[status.vigilance] < SEVERITY_RANK[prev]
                  ? 'improving'
                  : SEVERITY_RANK[status.vigilance] > SEVERITY_RANK[prev]
                    ? 'worsening'
                    : 'steady',
            )
            prevVigilanceRef.current = status.vigilance
          }
        })
        .catch((err) => {
          if (cancelled) return
          if (err instanceof ApiError && err.status === 404) {
            setRouteNotFound(true)
          } else {
            setRouteError(err instanceof ApiError ? err.message : 'Failed to load route')
          }
        })
        .finally(() => {
          if (!cancelled) setRouteLoading(false)
        })
    }
    poll()
    const id = setInterval(poll, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [routeId, token, alertsSeverity, alertsReviewed])

  // "This alert" is the default tab whenever a new alert gets focused; the operator can still
  // switch to "Route alerts" afterwards without losing the focus/URL.
  const [tab, setTab] = useState<'alert' | 'route'>(focusedAlertId ? 'alert' : 'route')
  useEffect(() => {
    if (focusedAlertId) setTab('alert')
  }, [focusedAlertId])

  function focusAlert(id: string) {
    if (isLegacy) {
      navigate(`/alerts/${id}`)
    } else {
      const next = new URLSearchParams(searchParams)
      next.set('alert', id)
      setSearchParams(next)
    }
  }

  async function save() {
    if (!focusedAlert) return
    setSaving(true)
    setAlertError(null)
    try {
      const updated = await reviewAlert(
        focusedAlert.id_alert,
        { reviewed_by_operator: reviewed, operator_notes: notes },
        token,
      )
      setFocusedAlert(updated)
      setSaved(true)
    } catch (err) {
      setAlertError(err instanceof ApiError ? err.message : 'Failed to save review')
    } finally {
      setSaving(false)
    }
  }

  // --- loading / not-found gates ---
  if (isLegacy && alertLoading && !focusedAlert) {
    return (
      <div style={{ padding: '24px 28px', color: 'var(--text-faint)', fontSize: 13 }}>
        Loading alert…
      </div>
    )
  }

  if (isLegacy && alertNotFound) {
    return (
      <NotFound
        backTo="/"
        backLabel="Back to Live operations"
        title="Alert not found"
        message={
          <>
            There is no alert with the identifier{' '}
            <span className="mono">{legacyAlertId}</span>.
          </>
        }
      />
    )
  }

  if (!routeId || (routeLoading && !route)) {
    return (
      <div style={{ padding: '24px 28px', color: 'var(--text-faint)', fontSize: 13 }}>
        Loading route…
      </div>
    )
  }

  if (routeNotFound || !route) {
    return (
      <NotFound
        backTo="/routes"
        backLabel="Back to Routes"
        title="Route not found"
        message={
          <>
            There is no route with the identifier <span className="mono">{routeId}</span>.
          </>
        }
      />
    )
  }

  const sev = focusedAlert
    ? severity[focusedAlert.severity_level]
    : liveStatus
      ? severity[liveStatus.vigilance]
      : { label: 'No live data', tone: 'neutral' as const }

  // Binary Not Drowsy / Drowsy scheme (root CLAUDE.md's drowsiness-class migration) — the old
  // 3-way Alert/Low vigilance/Drowsy split is retired. `ai_metadata` is null for a
  // panic_button alert (no camera evaluation happened) — see types.ts's doc comment.
  const scores = focusedAlert?.ai_metadata
    ? [
        {
          label: 'Not drowsy',
          value: focusedAlert.ai_metadata.scores.not_drowsy,
          color: 'var(--good)',
        },
        { label: 'Drowsy', value: focusedAlert.ai_metadata.scores.drowsy, color: 'var(--bad)' },
      ]
    : []

  const truckMarker: FleetMapMarker | null = liveStatus
    ? {
        id: routeId,
        position: toLatLng(liveStatus.current_coordinates),
        plate: truck ? `${truck.plate_number} · ${truck.company_number}` : '—',
        driver: driver ? `${driver.first_name} ${driver.last_name}` : '—',
        origin: route.origin_name,
        destination: route.destination_name,
        speedKmh: liveStatus.current_speed,
        vigilanceLabel: severity[liveStatus.vigilance].label,
        tone: severity[liveStatus.vigilance].tone,
        timestamp: liveStatus.timestamp,
      }
    : null

  const ctxItems = focusedAlert
    ? [
        { label: 'Truck', value: truck ? `${truck.plate_number} · ${truck.company_number}` : '—', mono: true },
        { label: 'Driver', value: driver ? `${driver.first_name} ${driver.last_name}` : '—' },
        { label: 'Route', value: `${route.origin_name} → ${route.destination_name}` },
        { label: 'Speed at event', value: `${focusedAlert.speed_at_event} km/h`, mono: true },
        { label: 'Source', value: SOURCE_LABEL[focusedAlert.source] },
      ]
    : [
        { label: 'Truck', value: truck ? `${truck.plate_number} · ${truck.company_number}` : '—', mono: true },
        { label: 'Driver', value: driver ? `${driver.first_name} ${driver.last_name}` : '—' },
        { label: 'Route', value: `${route.origin_name} → ${route.destination_name}` },
        {
          label: 'Current speed',
          value: liveStatus ? `${liveStatus.current_speed} km/h` : '—',
          mono: true,
        },
      ]

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        padding: '22px 28px',
        gap: 16,
        height: '100%',
        overflow: 'hidden',
      }}
    >
      <Link
        to={isLegacy ? '/' : '/routes'}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 12.5,
          color: 'var(--text-faint)',
        }}
      >
        <Icon name="chevron-left" size={14} />
        {isLegacy ? 'Back to Live operations' : 'Back to Routes'}
      </Link>

      {(alertError || routeError) && (
        <div role="alert" style={{ color: 'var(--danger, #e5484d)', fontSize: 13 }}>
          {alertError ?? routeError}
        </div>
      )}

      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: 16,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span
            style={{
              width: 38,
              height: 38,
              borderRadius: 9,
              background: sev.tone === 'bad' ? 'var(--bad-soft)' : 'var(--surface-2)',
              border: `1px solid ${sev.tone === 'bad' ? 'var(--bad)' : 'var(--border-soft)'}`,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
              color: sev.tone === 'bad' ? 'var(--bad)' : 'var(--text-faint)',
            }}
          >
            <Icon
              name={focusedAlert ? 'alert-triangle' : 'route'}
              size={19}
              strokeWidth={1.8}
            />
          </span>
          <div>
            <h1
              style={{
                fontFamily: 'var(--font-display)',
                fontWeight: 600,
                fontSize: 20,
                margin: 0,
              }}
            >
              {focusedAlert ? focusedAlert.alert_type : `${route.origin_name} → ${route.destination_name}`}
            </h1>
            <div
              style={{
                fontSize: 12.5,
                color: 'var(--text-faint)',
                marginTop: 2,
              }}
            >
              {focusedAlert ? (
                <>
                  Alert #{focusedAlert.id_alert} · {shortDate(focusedAlert.timestamp)},{' '}
                  {clock(focusedAlert.timestamp)} · {relativeTime(focusedAlert.timestamp)}
                </>
              ) : liveStatus ? (
                <>Last position update {relativeTime(liveStatus.timestamp)}</>
              ) : (
                'No live telemetry yet for this route'
              )}
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
          <span
            className={sev.tone === 'neutral' ? 'pill' : `pill pill--${sev.tone}`}
            style={{ padding: '6px 14px', fontSize: 11.5 }}
          >
            {sev.label.toUpperCase()}
            {focusedAlert ? ' SEVERITY' : ''}
          </span>
          {!focusedAlert && liveStatus && (
            <span
              className={
                TREND_TONE[trend] === 'neutral' ? 'pill' : `pill pill--${TREND_TONE[trend]}`
              }
              style={{ fontSize: 10.5, padding: '3px 9px' }}
            >
              {TREND_LABEL[trend]}
            </span>
          )}
          {focusedAlert && (
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
              {focusedAlert.related_alert_id && (
                <Link
                  to={`/alerts/${focusedAlert.related_alert_id}`}
                  className="pill"
                  style={{ fontSize: 10.5, padding: '3px 9px', textDecoration: 'none' }}
                >
                  Escalated from #{focusedAlert.related_alert_id}
                </Link>
              )}
              <span
                className={focusedAlert.resolved_at ? 'pill' : 'pill pill--warn'}
                style={{ fontSize: 10.5, padding: '3px 9px' }}
              >
                {focusedAlert.resolved_at ? `Resolved ${clock(focusedAlert.resolved_at)}` : 'Ongoing'}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* context strip */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: `repeat(${ctxItems.length}, minmax(0,1fr))`,
          gap: 14,
        }}
      >
        {ctxItems.map((item) => (
          <Ctx key={item.label} label={item.label}>
            {item.mono ? <span className="mono">{item.value}</span> : item.value}
          </Ctx>
        ))}
      </div>

      <div style={{ flex: 1, display: 'flex', gap: 18, minHeight: 0 }}>
        {/* map + (when focused) model output */}
        <div
          style={{
            flex: 1.3,
            display: 'flex',
            flexDirection: 'column',
            gap: 14,
            minHeight: 0,
          }}
        >
          <div
            style={{
              background: 'oklch(0.1 0.01 258)',
              border: '1px solid var(--border-soft)',
              borderRadius: 12,
              flex: 1,
              position: 'relative',
              overflow: 'hidden',
            }}
          >
            <FleetMap markers={truckMarker ? [truckMarker] : []} maxZoom={13} follow />
          </div>

          {focusedAlert && (
            <div className="panel" style={{ padding: '14px 16px' }}>
              {focusedAlert.ai_metadata ? (
                <>
                  <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 10 }}>
                    Model output ({focusedAlert.ai_metadata.model ?? 'unknown'})
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {scores.map((s) => (
                      <div key={s.label}>
                        <div
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            fontSize: 11.5,
                            color: 'var(--text-soft)',
                            marginBottom: 3,
                          }}
                        >
                          <span>{s.label}</span>
                          <span>{pct(s.value)}</span>
                        </div>
                        <div
                          style={{
                            height: 6,
                            borderRadius: 4,
                            background: 'var(--surface-3)',
                            overflow: 'hidden',
                          }}
                        >
                          <div
                            style={{
                              width: pct(s.value),
                              height: '100%',
                              background: s.color,
                            }}
                          />
                        </div>
                      </div>
                    ))}
                  </div>
                  {focusedAlert.grip_status && (
                    <div
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        fontSize: 11.5,
                        color: 'var(--text-soft)',
                        marginTop: 10,
                        paddingTop: 10,
                        borderTop: '1px solid var(--border-soft)',
                      }}
                    >
                      <span>Steering-wheel grip</span>
                      <span
                        className={focusedAlert.grip_status === 'bad' ? 'pill pill--bad' : 'pill'}
                        style={{ fontSize: 10.5, padding: '3px 9px' }}
                      >
                        {GRIP_LABEL[focusedAlert.grip_status]}
                      </span>
                    </div>
                  )}
                </>
              ) : (
                <>
                  <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 6 }}>
                    Trigger source
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text-soft)' }}>
                    {SOURCE_LABEL[focusedAlert.source]} — no camera/grip evaluation happens for
                    this alert type.
                  </div>
                </>
              )}
            </div>
          )}
        </div>

        {/* right panel */}
        <div
          className="panel"
          style={{
            width: 400,
            flexShrink: 0,
            padding: 18,
            display: 'flex',
            flexDirection: 'column',
            gap: 14,
            overflow: 'hidden',
          }}
        >
          <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
            {focusedAlertId && (
              <TabButton active={tab === 'alert'} onClick={() => setTab('alert')}>
                This alert
              </TabButton>
            )}
            <TabButton active={tab === 'route'} onClick={() => setTab('route')}>
              Route alerts
            </TabButton>
          </div>

          <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 16 }}>
            {tab === 'alert' && focusedAlertId && (
              <>
                {alertLoading && !focusedAlert && (
                  <div style={{ fontSize: 12.5, color: 'var(--text-faint)' }}>Loading alert…</div>
                )}
                {alertNotFound && (
                  <div style={{ fontSize: 12.5, color: 'var(--text-faint)' }}>
                    Alert not found.
                  </div>
                )}
                {focusedAlert && (
                  <>
                    {canReview && (
                      <div>
                        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>
                          Immediate actions
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                          <button className="btn btn--danger" style={{ justifyContent: 'flex-start' }}>
                            <Icon name="siren" size={16} strokeWidth={1.8} />
                            Trigger in-cab alarm
                          </button>
                          <button className="btn" style={{ justifyContent: 'flex-start' }}>
                            <Icon name="phone" size={16} />
                            Contact the driver
                          </button>
                          <button className="btn" style={{ justifyContent: 'flex-start' }}>
                            <Icon name="building" size={16} />
                            Notify logistics
                          </button>
                        </div>
                      </div>
                    )}

                    {canReview && <div style={{ height: 1, background: 'var(--border-soft)' }} />}

                    <div>
                      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>
                        Operator review
                      </div>
                      {canReview ? (
                        <>
                          <label
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              gap: 8,
                              fontSize: 12.5,
                              color: 'var(--text-soft)',
                              marginBottom: 10,
                            }}
                          >
                            <input
                              type="checkbox"
                              checked={reviewed}
                              onChange={(e) => {
                                setReviewed(e.target.checked)
                                setSaved(false)
                              }}
                              style={{
                                accentColor: 'var(--accent)',
                                width: 14,
                                height: 14,
                              }}
                            />
                            Mark alert as reviewed
                          </label>
                          <textarea
                            className="input"
                            rows={5}
                            placeholder="Operator notes — what was observed, what action was taken…"
                            value={notes}
                            onChange={(e) => {
                              setNotes(e.target.value)
                              setSaved(false)
                            }}
                          />
                        </>
                      ) : (
                        <div style={{ fontSize: 12.5, color: 'var(--text-soft)' }}>
                          <p style={{ margin: '0 0 8px' }}>
                            {reviewed ? 'Reviewed' : 'Not yet reviewed'}
                          </p>
                          {notes && <p style={{ margin: 0, color: 'var(--text-faint)' }}>{notes}</p>}
                        </div>
                      )}
                    </div>

                    {canReview && (
                      <button
                        className="btn btn--accent btn--block"
                        style={{ marginTop: 'auto', padding: 12 }}
                        onClick={save}
                        disabled={saved || saving}
                      >
                        {saving ? (
                          'Saving…'
                        ) : saved ? (
                          <>
                            <Icon name="check" size={15} strokeWidth={2} /> Saved
                          </>
                        ) : (
                          'Save and close alert'
                        )}
                      </button>
                    )}
                  </>
                )}
              </>
            )}

            {tab === 'route' && (
              <>
                <FilterChips options={SEVERITY_FILTERS} value={alertsSeverity} onChange={setAlertsSeverity} />
                <FilterChips options={REVIEWED_FILTERS} value={alertsReviewed} onChange={setAlertsReviewed} />
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {routeAlerts.map((a) => {
                    const s = severity[a.severity_level]
                    const isFocused = a.id_alert === focusedAlertId
                    return (
                      <button
                        key={a.id_alert}
                        onClick={() => focusAlert(a.id_alert)}
                        style={{
                          display: 'flex',
                          flexDirection: 'column',
                          gap: 3,
                          padding: 10,
                          borderRadius: 8,
                          textAlign: 'left',
                          cursor: 'pointer',
                          font: 'inherit',
                          color: 'inherit',
                          background: isFocused ? 'var(--surface-3)' : 'var(--surface-2)',
                          border: `1px solid ${isFocused ? 'var(--accent)' : 'var(--border-soft)'}`,
                          opacity: a.reviewed_by_operator ? 0.6 : 1,
                        }}
                      >
                        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                          <span style={{ fontSize: 12, fontWeight: 600 }}>{a.alert_type}</span>
                          <span
                            style={{
                              fontSize: 10.5,
                              fontWeight: 600,
                              color: a.reviewed_by_operator ? 'var(--text-faint)' : toneColor[s.tone],
                            }}
                          >
                            {a.reviewed_by_operator ? 'REVIEWED' : s.label.toUpperCase()}
                          </span>
                        </div>
                        <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>
                          {relativeTime(a.timestamp)}
                        </span>
                      </button>
                    )
                  })}
                  {routeAlerts.length === 0 && (
                    <div
                      style={{
                        fontSize: 12,
                        color: 'var(--text-faint)',
                        padding: 12,
                        textAlign: 'center',
                      }}
                    >
                      No alerts match these filters.
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      onClick={onClick}
      style={{
        fontSize: 12,
        fontWeight: 600,
        padding: '7px 12px',
        borderRadius: 8,
        border: 'none',
        cursor: 'pointer',
        font: 'inherit',
        background: active ? 'var(--surface-3)' : 'transparent',
        color: active ? 'var(--text)' : 'var(--text-faint)',
      }}
    >
      {children}
    </button>
  )
}

function NotFound({
  backTo,
  backLabel,
  title,
  message,
}: {
  backTo: string
  backLabel: string
  title: string
  message: ReactNode
}) {
  return (
    <div style={{ padding: '24px 28px' }}>
      <Link
        to={backTo}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 12.5,
          color: 'var(--text-faint)',
        }}
      >
        <Icon name="chevron-left" size={14} />
        {backLabel}
      </Link>
      <div
        style={{
          marginTop: 40,
          textAlign: 'center',
          color: 'var(--text-soft)',
        }}
      >
        <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 20 }}>{title}</h1>
        <p style={{ fontSize: 13, color: 'var(--text-faint)' }}>{message}</p>
      </div>
    </div>
  )
}

function Ctx({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="panel" style={{ padding: '12px 14px' }}>
      <div style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>{label}</div>
      <div style={{ fontSize: 14, fontWeight: 500, marginTop: 3 }}>{children}</div>
    </div>
  )
}
