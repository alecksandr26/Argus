import { useMemo, useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import Icon from '../components/Icon'
import {
  alertById,
  driverById,
  routeById,
  truckById,
} from '../data/fixtures'
import { clock, pct, relativeTime, shortDate } from '../utils/format'
import { severity } from '../utils/status'

/**
 * Mockup: "Control Tower — Alert triage". Looks the alert up by the `:alertId`
 * route param. The review checkbox + notes are local state and "Save" only
 * flips a local `saved` flag — wiring is `PUT /api/alerts/:id` with
 * `reviwed_by_operator` / `operator_notes` (INTEGRATION.md, AlertTriage row).
 */
export default function AlertTriage() {
  const { alertId } = useParams()
  const alert = alertId ? alertById(alertId) : undefined

  const ctx = useMemo(() => {
    if (!alert) return null
    const route = routeById(alert.id_route)
    return {
      route,
      driver: route && driverById(route.id_driver),
      truck: route && truckById(route.id_truck),
    }
  }, [alert])

  const [reviewed, setReviewed] = useState(alert?.reviwed_by_operator ?? false)
  const [notes, setNotes] = useState(alert?.operator_notes ?? '')
  const [saved, setSaved] = useState(false)

  if (!alert) {
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
  const scores = [
    { label: 'Alert', value: alert.ai_metadata.scores.alert, color: 'var(--good)' },
    {
      label: 'Low vigilance',
      value: alert.ai_metadata.scores.low_vigilance,
      color: 'var(--warn)',
    },
    { label: 'Drowsy', value: alert.ai_metadata.scores.drowsy, color: 'var(--bad)' },
  ]

  function save() {
    // TODO(INTEGRATION.md): PUT /api/alerts/:id { reviwed_by_operator, operator_notes }
    setSaved(true)
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
        <span
          className={
            sev.tone === 'neutral' ? 'pill' : `pill pill--${sev.tone}`
          }
          style={{ padding: '6px 14px', fontSize: 11.5 }}
        >
          {sev.label.toUpperCase()} SEVERITY
        </span>
      </div>

      {/* context strip */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, minmax(0,1fr))',
          gap: 14,
        }}
      >
        <Ctx label="Truck">
          <span className="mono">
            {ctx?.truck
              ? `${ctx.truck.plate_number} · ${ctx.truck.company_number}`
              : '—'}
          </span>
        </Ctx>
        <Ctx label="Driver">
          {ctx?.driver
            ? `${ctx.driver.first_name} ${ctx.driver.last_name}`
            : '—'}
        </Ctx>
        <Ctx label="Route">
          {ctx?.route
            ? `${ctx.route.origin_name} → ${ctx.route.destination_name}`
            : '—'}
        </Ctx>
        <Ctx label="Speed at event">
          <span className="mono">{alert.speed_at_event} km/h</span>
        </Ctx>
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
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                position: 'absolute',
                inset: 0,
                background:
                  'repeating-linear-gradient(135deg, oklch(0.16 0.015 258) 0 14px, oklch(0.13 0.015 258) 14px 28px)',
              }}
            />
            <span
              style={{
                position: 'relative',
                width: 58,
                height: 58,
                borderRadius: '50%',
                background: 'oklch(0.14 0.018 258 / 0.75)',
                border: '1px solid var(--border)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'var(--text)',
              }}
            >
              <Icon name="play" size={22} />
            </span>
            <span
              style={{
                position: 'absolute',
                top: 12,
                left: 14,
                fontSize: 10.5,
                background: 'oklch(0.14 0.018 258 / 0.75)',
                padding: '3px 9px',
                borderRadius: 6,
                color: 'var(--text-soft)',
              }}
            >
              {alert.ai_metadata.clip_seconds > 0
                ? `Captured clip · 00:0${alert.ai_metadata.clip_seconds}`
                : 'No clip'}
            </span>
            <span
              className="mono"
              style={{
                position: 'absolute',
                bottom: 12,
                right: 14,
                fontSize: 10,
                color: 'var(--text-faint)',
              }}
            >
              lat {alert.coordinates.lat.toFixed(4)}, lon{' '}
              {alert.coordinates.lon.toFixed(4)}
            </span>
          </div>

          <div className="panel" style={{ padding: '14px 16px' }}>
            <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 10 }}>
              Model output ({alert.ai_metadata.model})
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

          <div style={{ height: 1, background: 'var(--border-soft)' }} />

          <div>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>
              Operator review
            </div>
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
          </div>

          <button
            className="btn btn--accent btn--block"
            style={{ marginTop: 'auto', padding: 12 }}
            onClick={save}
            disabled={saved}
          >
            {saved ? (
              <>
                <Icon name="check" size={15} strokeWidth={2} /> Saved
              </>
            ) : (
              'Save and close alert'
            )}
          </button>
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
