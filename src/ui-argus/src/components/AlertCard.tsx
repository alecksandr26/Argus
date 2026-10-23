import { Link } from 'react-router-dom'
import { clock, relativeTime } from '../utils/format'
import { alertPosition, positionSummary } from '../utils/position'
import { REPLAYED_LABEL, alertTypeLabel, severity } from '../utils/status'
import type { Alert } from '../types'

const toneColor = {
  good: 'var(--good)',
  warn: 'var(--warn)',
  bad: 'var(--bad)',
  neutral: 'var(--text-faint)',
} as const

/**
 * One alert, as every incident feed and history list shows it (C3.7): its type label, severity,
 * time, its position (coordinates, "Last known (N old)" or "Position unknown") with a
 * "Show on map" button, the replayed badge for an alert recovered from the dead-letter queue,
 * and "Driver OK since" once the device reported the driver OK (informational only: it never
 * closes anything).
 */
export default function AlertCard({
  alert,
  onShowOnMap,
  focused = false,
  to,
}: {
  alert: Alert
  /** Shown as a "Show on map" button when the alert has a position. */
  onShowOnMap?: (alert: Alert) => void
  /** Highlights the card (the alert shown on the map, or the focused one). */
  focused?: boolean
  /** Where the alert's title links to (default: the alert's own page). */
  to?: string
}) {
  const sev = severity[alert.severity_level]
  const pos = alertPosition(alert)
  return (
    <div
      data-testid="alert-card"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 3,
        padding: '8px 10px',
        borderRadius: 8,
        background: focused ? 'var(--surface-3)' : 'var(--surface-2)',
        border: `1px solid ${focused ? 'var(--accent)' : 'var(--border-soft)'}`,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
        <Link
          to={to ?? `/alerts/${alert.id_alert}`}
          style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text)' }}
        >
          {alertTypeLabel(alert.alert_type)}
        </Link>
        <span style={{ fontSize: 10.5, fontWeight: 600, color: toneColor[sev.tone] }}>
          {sev.label.toUpperCase()}
        </span>
      </div>
      <span className="mono" style={{ fontSize: 11, color: 'var(--text-faint)' }}>
        {clock(alert.timestamp)} · {relativeTime(alert.timestamp)}
      </span>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <span
          className="mono"
          data-testid="alert-position"
          style={{
            fontSize: 11,
            color: pos.kind === 'live' ? 'var(--text-soft)' : 'var(--warn)',
          }}
        >
          {positionSummary(pos)}
        </span>
        {onShowOnMap && pos.coordinates && (
          <button
            type="button"
            className="btn btn--ghost"
            style={{ padding: '2px 8px', fontSize: 11, flexShrink: 0 }}
            onClick={() => onShowOnMap(alert)}
          >
            Show on map
          </button>
        )}
      </div>
      {(alert.delivery === 'dlq' || alert.driver_ok_at) && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 2 }}>
          {alert.delivery === 'dlq' && (
            <span className="pill pill--warn" style={{ fontSize: 10, padding: '2px 8px' }}>
              {REPLAYED_LABEL}
            </span>
          )}
          {alert.driver_ok_at && (
            <span className="pill pill--good" style={{ fontSize: 10, padding: '2px 8px' }}>
              Driver OK since {clock(alert.driver_ok_at)}
            </span>
          )}
        </div>
      )}
    </div>
  )
}
