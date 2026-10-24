import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import Icon from '../components/Icon'
import FilterChips from '../components/FilterChips'
import IncidentFeed, { IncidentCard } from '../components/IncidentFeed'
import CloseIncidentDialog from '../components/CloseIncidentDialog'
import CloseNotice from '../components/CloseNotice'
import RouteEndActions from '../components/RouteEndActions'
import FleetMap, { type MapFocus } from '../components/FleetMap'
import { useAuth } from '../context/AuthContext'
import { useLive } from '../hooks/useLive'
import { deescalateAlert, getAlert, reviewAlert } from '../api/alerts'
import { listIncidents } from '../api/incidents'
import { ApiError } from '../api/client'
import { clock, relativeTime, shortDate } from '../utils/format'
import {
  REPLAYED_LABEL,
  alertTypeLabel,
  incidentState,
  routeStatusBadge,
  severity,
} from '../utils/status'
import { toLatLng } from '../utils/geo'
import { alertPins, routeMarkers, trackPath } from '../utils/mapLayers'
import { alertPosition, pointPosition, positionSummary } from '../utils/position'
import { canCloseIncidents } from '../utils/incidents'
import type { Alert, AlertSeverity, AlertSource, Incident, Vigilance } from '../types'

/** Where an alert came from. `panic_button` exists only on old rows (the panic button was
 * removed); it stays so those rows still read. */
const SOURCE_LABEL: Record<AlertSource, string> = {
  fusion: 'Driver camera',
  panic_button: 'Panic button',
}

const SEVERITY_RANK: Record<AlertSeverity, number> = { low: 0, medium: 1, critical: 2 }

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

type IncidentsView = 'open' | 'closed'

const INCIDENT_VIEWS: { key: IncidentsView; label: string }[] = [
  { key: 'open', label: 'Open' },
  { key: 'closed', label: 'Closed' },
]

/**
 * "Route live view": live tracking + incident triage for one route. Mounted at two URL shapes
 * (see `App.tsx`): the canonical `/routes/:routeId/live` (+ optional `?alert=` focus), and the
 * legacy `/alerts/:alertId`, which resolves the route from the fetched alert first.
 *
 * Data: one `GET /api/live?route=<id>` poll every 5 s (`useLive`, paused while the tab is
 * hidden) gives the route (any status), its newest position and its open incidents; the whole
 * GPS path is seeded once from `GET /api/routes/{id}/track` and the live deltas are folded in
 * (C3.6 `[sync 2]`). Closed incidents come from `GET /api/incidents?route_id=&status=closed`.
 *
 * The map shows the truck (coloured by the route's `incident_state`, hollow when it's a last
 * known position), the path, and a pin per alert. It follows the truck; "Show on map" pauses
 * that to fly to an alert, and "Follow truck" resumes it. Guardian and root_admin close
 * incidents (a note is required for a critical one); root_admin and admin complete or stop the
 * route.
 *
 * After a close (from the Open feed or the focused alert's "Close incident"), a `role="status"`
 * confirmation ("… incident closed · Closed HH:MM", C3.7) stays above the tabs until the next
 * close or navigation; the incident leaves the Open list at once and the view stays where it is
 * (no switch to Closed).
 */
export default function RouteLiveView() {
  const { alertId: legacyAlertId, routeId: routeIdParam } = useParams<{
    alertId?: string
    routeId?: string
  }>()
  const [searchParams] = useSearchParams()
  const { session } = useAuth()
  const token = session!.token
  const role = session!.user.role
  const canReview = canCloseIncidents(role)

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

  const [deescTo, setDeescTo] = useState<AlertSeverity | ''>('')
  const [deescReason, setDeescReason] = useState('')
  const [deescalating, setDeescalating] = useState(false)

  /** Re-reads the focused alert (e.g. after its incident was closed) without touching the
   * review form the operator may be editing. */
  const reloadFocusedAlert = useCallback(
    (id: string) => getAlert(id, token).then(setFocusedAlert),
    [token],
  )

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
        setDeescTo('')
        setDeescReason('')
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

  // --- the route: one /api/live?route= poll, path seeded from /track ---
  const routeId = isLegacy ? (focusedAlert?.id_route ?? null) : (routeIdParam ?? null)
  const { live, loading, error, errorStatus, refresh } = useLive(token, routeId, Boolean(routeId))
  const route = live.routes.find((r) => r.id_route === routeId) ?? null

  // The last close's confirmation and the incidents closed here: hidden from the Open list right
  // away (a poll already in flight can't bring them back). Navigating resets both.
  const [closedNotice, setClosedNotice] = useState<Incident | null>(null)
  const [closedIds, setClosedIds] = useState<ReadonlySet<string>>(() => new Set())
  useEffect(() => {
    setClosedNotice(null)
    setClosedIds(new Set())
  }, [routeId, focusedAlertId])
  const openIncidents = useMemo(
    () => live.incidents.filter((i) => !closedIds.has(i.id_incident)),
    [live.incidents, closedIds],
  )
  const position = route?.latest_position ?? null

  // Closed incidents of this route (Incidents tab → Closed, and their pins on the map).
  const [closedIncidents, setClosedIncidents] = useState<Incident[]>([])
  const loadClosed = useCallback(() => {
    if (!routeId) return
    listIncidents(token, { route_id: routeId, status: 'closed' })
      .then(setClosedIncidents)
      .catch(() => {})
  }, [routeId, token])
  useEffect(() => {
    setClosedIncidents([])
    loadClosed()
  }, [loadClosed])

  // Trend of the newest point's vigilance between polls (unknown breaks it).
  const [trend, setTrend] = useState<Trend>('unknown')
  const prevVigilanceRef = useRef<Vigilance | null>(null)
  useEffect(() => {
    const v = position?.vigilance
    if (!v) return
    if (v === 'unknown') {
      setTrend('unknown')
      prevVigilanceRef.current = null
      return
    }
    const prev = prevVigilanceRef.current
    if (prev !== null && prev !== 'unknown') {
      setTrend(
        SEVERITY_RANK[v] < SEVERITY_RANK[prev]
          ? 'improving'
          : SEVERITY_RANK[v] > SEVERITY_RANK[prev]
            ? 'worsening'
            : 'steady',
      )
    }
    prevVigilanceRef.current = v
    // A new point, not a new object, is what moves the trend.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [position?.t])

  // "This alert" is the default tab whenever an alert gets focused.
  const [tab, setTab] = useState<'alert' | 'incidents'>(focusedAlertId ? 'alert' : 'incidents')
  useEffect(() => {
    if (focusedAlertId) setTab('alert')
  }, [focusedAlertId])
  const [incidentsView, setIncidentsView] = useState<IncidentsView>('open')

  const [focus, setFocus] = useState<MapFocus | null>(null)
  const focusNonce = useRef(0)
  function showOnMap(a: Alert) {
    if (!a.coordinates) return
    focusNonce.current += 1
    setFocus({ position: toLatLng(a.coordinates), pinId: a.id_alert, nonce: focusNonce.current })
  }

  const [closing, setClosing] = useState<Incident | null>(null)
  function afterClose(inc: Incident) {
    setClosedNotice(inc)
    setClosedIds((prev) => new Set(prev).add(inc.id_incident))
    void refresh()
    loadClosed()
    if (focusedAlertId) reloadFocusedAlert(focusedAlertId).catch(() => {})
  }

  const alertHref = (a: Alert) =>
    isLegacy ? `/alerts/${a.id_alert}` : `/routes/${a.id_route}/live?alert=${a.id_alert}`

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

  async function deescalate() {
    if (!focusedAlert || !deescTo) return
    setDeescalating(true)
    setAlertError(null)
    try {
      const updated = await deescalateAlert(
        focusedAlert.id_alert,
        { severity_level: deescTo, reason: deescReason.trim() },
        token,
      )
      setFocusedAlert(updated)
      setReviewed(updated.reviewed_by_operator)
      setDeescTo('')
      setDeescReason('')
      void refresh()
    } catch (err) {
      setAlertError(err instanceof ApiError ? err.message : 'Failed to de-escalate alert')
    } finally {
      setDeescalating(false)
    }
  }

  // --- map layers ---
  const markers = useMemo(() => (route ? routeMarkers([route]) : []), [route])
  const track = routeId ? live.track[routeId] : undefined
  const paths = useMemo(() => {
    const p = routeId && track ? trackPath(routeId, track) : null
    return p ? [p] : []
  }, [routeId, track])
  const pins = useMemo(
    () =>
      alertPins([
        ...(focusedAlert ? [focusedAlert] : []),
        ...openIncidents.flatMap((i) => i.alerts),
        ...closedIncidents.flatMap((i) => i.alerts),
      ]),
    [focusedAlert, openIncidents, closedIncidents],
  )

  // The focused alert's incident, if it is still open (closing happens per incident).
  const focusedIncident = focusedAlert?.id_incident
    ? (openIncidents.find((i) => i.id_incident === focusedAlert.id_incident) ?? null)
    : null

  // --- loading / not-found gates ---
  if (isLegacy && alertLoading && !focusedAlert) {
    return <Message>Loading alert…</Message>
  }

  if (isLegacy && alertNotFound) {
    return (
      <NotFound
        backTo="/"
        backLabel="Back to Live operations"
        title="Alert not found"
        message={
          <>
            There is no alert with the identifier <span className="mono">{legacyAlertId}</span>.
          </>
        }
      />
    )
  }

  if (errorStatus === 404) {
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

  if (!routeId || !route) {
    if (routeId && !loading && error) {
      return (
        <div role="alert" style={{ padding: '24px 28px', color: 'var(--danger, #e5484d)', fontSize: 13 }}>
          {error}
        </div>
      )
    }
    return <Message>Loading route…</Message>
  }

  const routeName = `${route.origin_name} → ${route.destination_name}`
  const state = incidentState[route.incident_state]
  const head = focusedAlert ? severity[focusedAlert.severity_level] : state
  const focusedPos = focusedAlert ? alertPosition(focusedAlert) : null
  const truckPos = position ? pointPosition(position) : null
  const statusBadge = routeStatusBadge(route)

  const ctxItems: { label: string; value: ReactNode; mono?: boolean }[] = [
    { label: 'Truck', value: route.truck_plate_number ?? '—', mono: true },
    { label: 'Driver', value: route.driver_full_name ?? '—' },
    { label: 'Route', value: routeName },
    focusedAlert
      ? {
          label: 'Speed at event',
          value: focusedAlert.speed_at_event == null ? '—' : `${focusedAlert.speed_at_event} km/h`,
          mono: true,
        }
      : {
          label: 'Current speed',
          value: position && !position.last_known ? `${position.speed} km/h` : '—',
          mono: true,
        },
    focusedAlert
      ? { label: 'Position at event', value: positionSummary(focusedPos!), mono: true }
      : {
          label: 'Position',
          value: truckPos ? positionSummary(truckPos) : 'No position yet',
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
        style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5, color: 'var(--text-faint)' }}
      >
        <Icon name="chevron-left" size={14} />
        {isLegacy ? 'Back to Live operations' : 'Back to Routes'}
      </Link>

      {(alertError || error) && (
        <div role="alert" style={{ color: 'var(--danger, #e5484d)', fontSize: 13 }}>
          {alertError ?? error}
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span
            style={{
              width: 38,
              height: 38,
              borderRadius: 9,
              background: head.tone === 'bad' ? 'var(--bad-soft)' : 'var(--surface-2)',
              border: `1px solid ${head.tone === 'bad' ? 'var(--bad)' : 'var(--border-soft)'}`,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
              color: head.tone === 'bad' ? 'var(--bad)' : 'var(--text-faint)',
            }}
          >
            <Icon name={focusedAlert ? 'alert-triangle' : 'route'} size={19} strokeWidth={1.8} />
          </span>
          <div>
            <h1 style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 20, margin: 0 }}>
              {focusedAlert ? alertTypeLabel(focusedAlert.alert_type) : routeName}
            </h1>
            <div style={{ fontSize: 12.5, color: 'var(--text-faint)', marginTop: 2 }}>
              {focusedAlert ? (
                <>
                  Alert #{focusedAlert.id_alert} · {shortDate(focusedAlert.timestamp)},{' '}
                  {clock(focusedAlert.timestamp)} · {relativeTime(focusedAlert.timestamp)}
                </>
              ) : position ? (
                <>Last position update {relativeTime(position.t)}</>
              ) : (
                'No live telemetry yet for this route'
              )}
            </div>
            {route.stop_reason && (
              <div style={{ fontSize: 12, color: 'var(--warn)', marginTop: 4 }} data-testid="stop-reason">
                Stopped{route.stopped_at ? ` ${clock(route.stopped_at)}` : ''}: {route.stop_reason}
              </div>
            )}
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
          <span
            className={head.tone === 'neutral' ? 'pill' : `pill pill--${head.tone}`}
            style={{ padding: '6px 14px', fontSize: 11.5 }}
          >
            {head.label.toUpperCase()}
            {focusedAlert ? ' SEVERITY' : ''}
          </span>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            <span
              className={statusBadge.tone === 'neutral' ? 'pill' : `pill pill--${statusBadge.tone}`}
              style={{ fontSize: 10.5, padding: '3px 9px' }}
            >
              {statusBadge.label}
            </span>
            {!focusedAlert && position && (
              <span
                className={TREND_TONE[trend] === 'neutral' ? 'pill' : `pill pill--${TREND_TONE[trend]}`}
                style={{ fontSize: 10.5, padding: '3px 9px' }}
              >
                {TREND_LABEL[trend]}
              </span>
            )}
            {focusedAlert && (
              <>
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
                  className={focusedAlert.closed_at ? 'pill' : 'pill pill--warn'}
                  style={{ fontSize: 10.5, padding: '3px 9px' }}
                >
                  {focusedAlert.closed_at ? `Closed ${clock(focusedAlert.closed_at)}` : 'Open'}
                </span>
                {focusedAlert.driver_ok_at && (
                  <span className="pill pill--good" style={{ fontSize: 10.5, padding: '3px 9px' }}>
                    Driver OK since {clock(focusedAlert.driver_ok_at)}
                  </span>
                )}
                {focusedAlert.delivery === 'dlq' && (
                  <span className="pill pill--warn" style={{ fontSize: 10.5, padding: '3px 9px' }}>
                    {REPLAYED_LABEL}
                  </span>
                )}
              </>
            )}
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <RouteEndActions route={route} role={role} token={token} onChanged={() => void refresh()} />
          </div>
        </div>
      </div>

      {/* context strip */}
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${ctxItems.length}, minmax(0,1fr))`, gap: 14 }}>
        {ctxItems.map((item) => (
          <Ctx key={item.label} label={item.label}>
            {item.mono ? <span className="mono">{item.value}</span> : item.value}
          </Ctx>
        ))}
      </div>

      <div style={{ flex: 1, display: 'flex', gap: 18, minHeight: 0 }}>
        {/* map */}
        <div style={{ flex: 1.3, display: 'flex', flexDirection: 'column', gap: 14, minHeight: 0 }}>
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
            <FleetMap markers={markers} paths={paths} pins={pins} focus={focus} maxZoom={14} follow />
            {focus && markers.length > 0 && (
              <button
                type="button"
                className="btn"
                onClick={() => setFocus(null)}
                style={{
                  position: 'absolute',
                  top: 12,
                  right: 12,
                  zIndex: 1000,
                  padding: '5px 12px',
                  fontSize: 12,
                }}
              >
                <Icon name="route" size={14} />
                Follow truck
              </button>
            )}
          </div>

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
          <CloseNotice incident={closedNotice} />

          <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
            {focusedAlertId && (
              <TabButton active={tab === 'alert'} onClick={() => setTab('alert')}>
                This alert
              </TabButton>
            )}
            <TabButton active={tab === 'incidents'} onClick={() => setTab('incidents')}>
              Incidents ({openIncidents.length} open)
            </TabButton>
          </div>

          <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 16 }}>
            {tab === 'alert' && focusedAlertId && (
              <>
                {alertLoading && !focusedAlert && (
                  <div style={{ fontSize: 12.5, color: 'var(--text-faint)' }}>Loading alert…</div>
                )}
                {alertNotFound && (
                  <div style={{ fontSize: 12.5, color: 'var(--text-faint)' }}>Alert not found.</div>
                )}
                {focusedAlert && (
                  <>
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>Where</div>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                        <span
                          className="mono"
                          data-testid="focused-alert-position"
                          style={{
                            fontSize: 12,
                            color: focusedPos!.kind === 'live' ? 'var(--text-soft)' : 'var(--warn)',
                          }}
                        >
                          {positionSummary(focusedPos!)}
                        </span>
                        {focusedPos!.coordinates && (
                          <button
                            type="button"
                            className="btn btn--ghost"
                            style={{ padding: '2px 8px', fontSize: 11 }}
                            onClick={() => showOnMap(focusedAlert)}
                          >
                            Show on map
                          </button>
                        )}
                      </div>
                      <div style={{ fontSize: 11.5, color: 'var(--text-faint)', marginTop: 4 }}>
                        Source: {SOURCE_LABEL[focusedAlert.source]}
                      </div>
                    </div>

                    {canReview && focusedIncident && (
                      <div>
                        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>Incident</div>
                        <div style={{ fontSize: 12, color: 'var(--text-soft)', marginBottom: 8 }}>
                          {severity[focusedIncident.severity].label} incident ·{' '}
                          {focusedIncident.alert_count} alert
                          {focusedIncident.alert_count === 1 ? '' : 's'}. Closing it closes all of
                          them.
                        </div>
                        <button
                          type="button"
                          className="btn btn--accent btn--block"
                          onClick={() => setClosing(focusedIncident)}
                        >
                          Close incident
                        </button>
                      </div>
                    )}

                    {canReview && <div style={{ height: 1, background: 'var(--border-soft)' }} />}

                    <div>
                      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>Operator review</div>
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
                              style={{ accentColor: 'var(--accent)', width: 14, height: 14 }}
                            />
                            Mark alert as reviewed
                          </label>
                          <textarea
                            className="input"
                            rows={4}
                            placeholder="Operator notes — what was observed, what action was taken…"
                            value={notes}
                            onChange={(e) => {
                              setNotes(e.target.value)
                              setSaved(false)
                            }}
                          />
                          <button
                            className="btn btn--block"
                            style={{ marginTop: 8 }}
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
                              'Save review'
                            )}
                          </button>
                        </>
                      ) : (
                        <div style={{ fontSize: 12.5, color: 'var(--text-soft)' }}>
                          <p style={{ margin: '0 0 8px' }}>{reviewed ? 'Reviewed' : 'Not yet reviewed'}</p>
                          {notes && <p style={{ margin: 0, color: 'var(--text-faint)' }}>{notes}</p>}
                        </div>
                      )}
                    </div>

                    {focusedAlert.original_severity_level && (
                      <div style={{ fontSize: 12, color: 'var(--text-soft)' }} data-testid="deescalation-note">
                        De-escalated from{' '}
                        <strong>{severity[focusedAlert.original_severity_level].label}</strong> to{' '}
                        <strong>{severity[focusedAlert.severity_level].label}</strong>
                        {focusedAlert.deescalated_at && (
                          <>
                            {' '}
                            · {shortDate(focusedAlert.deescalated_at)} {clock(focusedAlert.deescalated_at)}
                          </>
                        )}
                        {focusedAlert.deescalation_reason && (
                          <p style={{ margin: '4px 0 0', color: 'var(--text-faint)' }}>
                            {focusedAlert.deescalation_reason}
                          </p>
                        )}
                      </div>
                    )}

                    {canReview && SEVERITY_RANK[focusedAlert.severity_level] > 0 && !focusedAlert.closed_at && (
                      <div>
                        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>De-escalate severity</div>
                        <div style={{ fontSize: 12, color: 'var(--text-faint)', marginBottom: 10 }}>
                          Only after confirming with the driver. The device never lowers a severity
                          on its own.
                        </div>
                        <select
                          className="input"
                          aria-label="New severity"
                          value={deescTo}
                          onChange={(e) => setDeescTo(e.target.value as AlertSeverity | '')}
                          style={{ marginBottom: 8 }}
                        >
                          <option value="">Choose a lower severity…</option>
                          {(['medium', 'low'] as AlertSeverity[])
                            .filter((s) => SEVERITY_RANK[s] < SEVERITY_RANK[focusedAlert.severity_level])
                            .map((s) => (
                              <option key={s} value={s}>
                                {severity[s].label}
                              </option>
                            ))}
                        </select>
                        <textarea
                          className="input"
                          rows={2}
                          aria-label="De-escalation reason"
                          placeholder="Reason, e.g. called the driver, false positive"
                          value={deescReason}
                          onChange={(e) => setDeescReason(e.target.value)}
                          style={{ marginBottom: 8 }}
                        />
                        <button
                          className="btn btn--block"
                          onClick={deescalate}
                          disabled={!deescTo || !deescReason.trim() || deescalating}
                        >
                          {deescalating ? 'De-escalating…' : 'De-escalate'}
                        </button>
                      </div>
                    )}
                  </>
                )}
              </>
            )}

            {tab === 'incidents' && (
              <>
                <FilterChips options={INCIDENT_VIEWS} value={incidentsView} onChange={setIncidentsView} />
                {incidentsView === 'open' ? (
                  <IncidentFeed
                    incidents={openIncidents}
                    role={role}
                    token={token}
                    onClosed={afterClose}
                    onShowOnMap={showOnMap}
                    focusedAlertId={focusedAlertId ?? null}
                    routeHeaders={false}
                    alertHref={alertHref}
                    emptyLabel="No open incidents on this route."
                  />
                ) : closedIncidents.length ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {closedIncidents.map((inc) => (
                      <IncidentCard
                        key={inc.id_incident}
                        incident={inc}
                        canClose={false}
                        onShowOnMap={showOnMap}
                        focusedAlertId={focusedAlertId ?? null}
                        alertHref={alertHref}
                      />
                    ))}
                  </div>
                ) : (
                  <div style={{ fontSize: 12, color: 'var(--text-faint)', padding: 12, textAlign: 'center' }}>
                    No closed incidents on this route.
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>

      {closing && (
        <CloseIncidentDialog
          incident={closing}
          token={token}
          onCancel={() => setClosing(null)}
          onClosed={(inc) => {
            setClosing(null)
            afterClose(inc)
          }}
        />
      )}
    </div>
  )
}

function Message({ children }: { children: ReactNode }) {
  return <div style={{ padding: '24px 28px', color: 'var(--text-faint)', fontSize: 13 }}>{children}</div>
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
      type="button"
      aria-pressed={active}
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
        style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5, color: 'var(--text-faint)' }}
      >
        <Icon name="chevron-left" size={14} />
        {backLabel}
      </Link>
      <div style={{ marginTop: 40, textAlign: 'center', color: 'var(--text-soft)' }}>
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
