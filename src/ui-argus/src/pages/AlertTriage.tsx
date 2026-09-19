import { useEffect, useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import Icon from '../components/Icon'
import { useAuth } from '../context/AuthContext'
import { getAlert, reviewAlert } from '../api/alerts'
import { getRoute } from '../api/routes'
import { listDrivers } from '../api/drivers'
import { listTrucks } from '../api/trucks'
import { ApiError } from '../api/client'
import { clock, pct, relativeTime, shortDate } from '../utils/format'
import { severity } from '../utils/status'
import { toLatLng } from '../utils/geo'
import FleetMap, { type FleetMapMarker } from '../components/FleetMap'
import type { Alert, AlertSource, Driver, GripStatus, Route, Truck } from '../types'

const SOURCE_LABEL: Record<AlertSource, string> = {
  fusion: 'Camera + grip sensor (fused)',
  panic_button: 'Driver-activated panic button',
}

const GRIP_LABEL: Record<GripStatus, string> = {
  good: 'Good',
  bad: 'Bad',
}

/**
 * "Control Tower — Alert triage" against the real `GET`/`PUT /api/alerts/:id`. Loading and
 * "not found" are now genuinely distinct states (they were conflated in the fixture-era
 * version). Review controls (checkbox/notes/Save) are `root_admin`/`guardian` only — matches
 * `review_alert`'s real backend `require_role`; `admin`/`truck_driver` see a read-only summary.
 */
export default function AlertTriage() {
  const { alertId } = useParams()
  const { session } = useAuth()
  const token = session!.token
  const canReview = session!.user.role === 'root_admin' || session!.user.role === 'guardian'

  const [alert, setAlert] = useState<Alert | null>(null)
  const [route, setRoute] = useState<Route | null>(null)
  const [driver, setDriver] = useState<Driver | null>(null)
  const [truck, setTruck] = useState<Truck | null>(null)
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [reviewed, setReviewed] = useState(false)
  const [notes, setNotes] = useState('')
  const [saved, setSaved] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!alertId) {
      setLoading(false)
      setNotFound(true)
      return
    }
    let cancelled = false
    setLoading(true)
    setNotFound(false)
    getAlert(alertId, token)
      .then(async (a) => {
        if (cancelled) return
        setAlert(a)
        setReviewed(a.reviewed_by_operator)
        setNotes(a.operator_notes)
        const [r, drivers, trucks] = await Promise.all([
          getRoute(a.id_route, token),
          listDrivers(token),
          listTrucks(token),
        ])
        if (cancelled) return
        setRoute(r)
        setDriver(drivers.find((d) => d.id_driver === r.id_driver) ?? null)
        setTruck(trucks.find((t) => t.id_truck === r.id_truck) ?? null)
      })
      .catch((err) => {
        if (cancelled) return
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true)
        } else {
          setError(err instanceof ApiError ? err.message : 'Failed to load alert')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [alertId, token])

  async function save() {
    if (!alert) return
    setSaving(true)
    setError(null)
    try {
      const updated = await reviewAlert(
        alert.id_alert,
        { reviewed_by_operator: reviewed, operator_notes: notes },
        token,
      )
      setAlert(updated)
      setSaved(true)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to save review')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div style={{ padding: '24px 28px', color: 'var(--text-faint)', fontSize: 13 }}>
        Loading alert…
      </div>
    )
  }

  if (notFound || !alert) {
    return (
      <div style={{ padding: '24px 28px' }}>
        <Link
          to="/"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            fontSize: 12.5,
            color: 'var(--text-faint)',
          }}
        >
          <Icon name="chevron-left" size={14} />
          Back to Live operations
        </Link>
        <div
          style={{
            marginTop: 40,
            textAlign: 'center',
            color: 'var(--text-soft)',
          }}
        >
          <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 20 }}>
            Alert not found
          </h1>
          <p style={{ fontSize: 13, color: 'var(--text-faint)' }}>
            There is no alert with the identifier{' '}
            <span className="mono">{alertId}</span>.
          </p>
        </div>
      </div>
    )
  }

  const sev = severity[alert.severity_level]
  // Binary Not Drowsy / Drowsy scheme (root CLAUDE.md's drowsiness-class migration) — the old
  // 3-way Alert/Low vigilance/Drowsy split is retired. `ai_metadata` is null for a
  // panic_button alert (no camera evaluation happened) — see types.ts's doc comment.
  const scores = alert.ai_metadata
    ? [
        {
          label: 'Not drowsy',
          value: alert.ai_metadata.scores.not_drowsy,
          color: 'var(--good)',
        },
        { label: 'Drowsy', value: alert.ai_metadata.scores.drowsy, color: 'var(--bad)' },
      ]
    : []

  const alertMarker: FleetMapMarker = {
    id: alert.id_alert,
    position: toLatLng(alert.coordinates),
    plate: truck ? `${truck.plate_number} · ${truck.company_number}` : '—',
    driver: driver ? `${driver.first_name} ${driver.last_name}` : '—',
    origin: route?.origin_name ?? '—',
    destination: route?.destination_name ?? '—',
    speedKmh: alert.speed_at_event,
    vigilanceLabel: sev.label,
    tone: sev.tone,
    timestamp: alert.timestamp,
  }

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
        to="/"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 12.5,
          color: 'var(--text-faint)',
        }}
      >
        <Icon name="chevron-left" size={14} />
        Back to Live operations
      </Link>

      {error && (
        <div role="alert" style={{ color: 'var(--danger, #e5484d)', fontSize: 13 }}>
          {error}
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
              background: 'var(--bad-soft)',
              border: '1px solid var(--bad)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
              color: 'var(--bad)',
            }}
          >
            <Icon name="alert-triangle" size={19} strokeWidth={1.8} />
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
              {alert.alert_type}
            </h1>
            <div
              style={{
                fontSize: 12.5,
                color: 'var(--text-faint)',
                marginTop: 2,
              }}
            >
              Alert #{alert.id_alert} · {shortDate(alert.timestamp)},{' '}
              {clock(alert.timestamp)} · {relativeTime(alert.timestamp)}
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
          <span
            className={
              sev.tone === 'neutral' ? 'pill' : `pill pill--${sev.tone}`
            }
            style={{ padding: '6px 14px', fontSize: 11.5 }}
          >
            {sev.label.toUpperCase()} SEVERITY
          </span>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            {alert.related_alert_id && (
              <Link
                to={`/alerts/${alert.related_alert_id}`}
                className="pill"
                style={{ fontSize: 10.5, padding: '3px 9px', textDecoration: 'none' }}
              >
                Escalated from #{alert.related_alert_id}
              </Link>
            )}
            <span
              className={alert.resolved_at ? 'pill' : 'pill pill--warn'}
              style={{ fontSize: 10.5, padding: '3px 9px' }}
            >
              {alert.resolved_at ? `Resolved ${clock(alert.resolved_at)}` : 'Ongoing'}
            </span>
          </div>
        </div>
      </div>

      {/* context strip */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(5, minmax(0,1fr))',
          gap: 14,
        }}
      >
        <Ctx label="Truck">
          <span className="mono">
            {truck ? `${truck.plate_number} · ${truck.company_number}` : '—'}
          </span>
        </Ctx>
        <Ctx label="Driver">
          {driver ? `${driver.first_name} ${driver.last_name}` : '—'}
        </Ctx>
        <Ctx label="Route">
          {route ? `${route.origin_name} → ${route.destination_name}` : '—'}
        </Ctx>
        <Ctx label="Speed at event">
          <span className="mono">{alert.speed_at_event} km/h</span>
        </Ctx>
        <Ctx label="Source">{SOURCE_LABEL[alert.source]}</Ctx>
      </div>

      <div style={{ flex: 1, display: 'flex', gap: 18, minHeight: 0 }}>
        {/* media + model */}
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
            <FleetMap markers={[alertMarker]} maxZoom={13} />
          </div>

          <div className="panel" style={{ padding: '14px 16px' }}>
            {alert.ai_metadata ? (
              <>
                <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 10 }}>
                  Model output ({alert.ai_metadata.model ?? 'unknown'})
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
                {alert.grip_status && (
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
                      className={
                        alert.grip_status === 'bad' ? 'pill pill--bad' : 'pill'
                      }
                      style={{ fontSize: 10.5, padding: '3px 9px' }}
                    >
                      {GRIP_LABEL[alert.grip_status]}
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
                  {SOURCE_LABEL[alert.source]} — no camera/grip evaluation happens for this
                  alert type.
                </div>
              </>
            )}
          </div>
        </div>

        {/* triage panel */}
        <div
          className="panel"
          style={{
            width: 400,
            flexShrink: 0,
            padding: 18,
            display: 'flex',
            flexDirection: 'column',
            gap: 16,
            overflowY: 'auto',
          }}
        >
          {canReview && (
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>
                Immediate actions
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <button
                  className="btn btn--danger"
                  style={{ justifyContent: 'flex-start' }}
                >
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
        </div>
      </div>
    </div>
  )
}

function Ctx({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <div className="panel" style={{ padding: '12px 14px' }}>
      <div style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>{label}</div>
      <div style={{ fontSize: 14, fontWeight: 500, marginTop: 3 }}>
        {children}
      </div>
    </div>
  )
}
