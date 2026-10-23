import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import AlertCard from './AlertCard'
import CloseIncidentDialog from './CloseIncidentDialog'
import { clock, relativeTime } from '../utils/format'
import { canCloseIncidents, groupIncidentFeed } from '../utils/incidents'
import { REPLAYED_LABEL, incidentTier, severity } from '../utils/status'
import type { Alert, Incident, Role } from '../types'

const toneColor = {
  good: 'var(--good)',
  warn: 'var(--warn)',
  bad: 'var(--bad)',
  neutral: 'var(--text-faint)',
} as const

/**
 * The incident feed of Live operations and the route view (C3.4): tier sections in the order
 * Critical → Medium → Older than 1 h, and inside a section the incidents of one route under one
 * route header (routes ordered by their newest incident, incidents newest first). The tier is the
 * server's; this only sorts and groups (`groupIncidentFeed`).
 *
 * Each incident shows its alerts (newest first, each with its position), "Driver OK since", the
 * replayed badge, and — for guardian and root_admin — "Close incident", which opens the close
 * dialog (a note is required for a critical incident).
 */
export default function IncidentFeed({
  incidents,
  role,
  token,
  onClosed,
  onShowOnMap,
  focusedAlertId = null,
  routeHeaders = true,
  emptyLabel = 'No open incidents.',
}: {
  incidents: readonly Incident[]
  role: Role
  token: string
  /** After a successful close (the caller re-polls). */
  onClosed?: (incident: Incident) => void
  onShowOnMap?: (alert: Alert) => void
  focusedAlertId?: string | null
  /** Show the route header above each route's incidents (off in the single-route view). */
  routeHeaders?: boolean
  emptyLabel?: string
}) {
  const [closing, setClosing] = useState<Incident | null>(null)
  const sections = useMemo(() => groupIncidentFeed(incidents), [incidents])
  const canClose = canCloseIncidents(role)

  if (sections.length === 0) {
    return (
      <div style={{ fontSize: 12, color: 'var(--text-faint)', padding: 12, textAlign: 'center' }}>
        {emptyLabel}
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {sections.map((section) => {
        const tier = incidentTier[section.tier]
        return (
          <section
            key={section.tier}
            aria-label={`${tier.label} incidents`}
            data-testid={`tier-${section.tier}`}
            style={{ display: 'flex', flexDirection: 'column', gap: 8 }}
          >
            <h3
              style={{
                margin: 0,
                fontSize: 11,
                fontWeight: 600,
                letterSpacing: 0.4,
                textTransform: 'uppercase',
                color: toneColor[tier.tone],
              }}
            >
              {tier.label} · {section.count}
            </h3>
            {section.routes.map((group) => (
              <div
                key={group.id_route}
                data-testid="incident-route-group"
                style={{ display: 'flex', flexDirection: 'column', gap: 6 }}
              >
                {routeHeaders && (
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      gap: 8,
                    }}
                  >
                    <Link
                      to={`/routes/${group.id_route}/live`}
                      className="mono"
                      style={{ fontSize: 11.5, color: 'var(--text-soft)' }}
                    >
                      {group.truck_plate_number ?? '—'} · {group.driver_full_name ?? '—'}
                    </Link>
                    {group.route_ended && (
                      <span className="pill" style={{ fontSize: 10, padding: '2px 8px' }}>
                        Route ended
                      </span>
                    )}
                  </div>
                )}
                {group.incidents.map((inc) => (
                  <IncidentCard
                    key={inc.id_incident}
                    incident={inc}
                    canClose={canClose}
                    onClose={() => setClosing(inc)}
                    onShowOnMap={onShowOnMap}
                    focusedAlertId={focusedAlertId}
                  />
                ))}
              </div>
            ))}
          </section>
        )
      })}
      {closing && (
        <CloseIncidentDialog
          incident={closing}
          token={token}
          onCancel={() => setClosing(null)}
          onClosed={(inc) => {
            setClosing(null)
            onClosed?.(inc)
          }}
        />
      )}
    </div>
  )
}

export function IncidentCard({
  incident,
  canClose,
  onClose,
  onShowOnMap,
  focusedAlertId = null,
}: {
  incident: Incident
  canClose: boolean
  onClose?: () => void
  onShowOnMap?: (alert: Alert) => void
  focusedAlertId?: string | null
}) {
  const sev = severity[incident.severity]
  const alertsNewestFirst = [...incident.alerts].reverse()
  const closed = incident.closed_at !== null
  return (
    <article
      data-testid="incident-card"
      aria-label={`${sev.label} incident`}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        padding: 10,
        borderRadius: 9,
        background: sev.tone === 'bad' && !closed ? 'var(--bad-soft)' : 'var(--surface)',
        border: `1px solid ${sev.tone === 'bad' && !closed ? 'var(--bad)' : 'var(--border-soft)'}`,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span className={`pill pill--${sev.tone}`} style={{ fontSize: 10.5, padding: '2px 8px' }}>
          {sev.label}
        </span>
        <span style={{ fontSize: 11.5, color: 'var(--text-soft)' }}>
          {incident.alert_count} alert{incident.alert_count === 1 ? '' : 's'}
        </span>
        <span className="mono" style={{ fontSize: 11, color: 'var(--text-faint)' }}>
          {clock(incident.first_alert_at)}
          {incident.last_alert_at !== incident.first_alert_at
            ? `–${clock(incident.last_alert_at)}`
            : ''}{' '}
          · {relativeTime(incident.last_alert_at)}
        </span>
        {canClose && !closed && onClose && (
          <button
            type="button"
            className="btn btn--ghost"
            style={{ marginLeft: 'auto', padding: '3px 10px', fontSize: 11.5 }}
            onClick={onClose}
          >
            Close incident
          </button>
        )}
      </div>
      {(incident.driver_ok_at || incident.has_replayed || closed) && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {incident.driver_ok_at && (
            <span className="pill pill--good" style={{ fontSize: 10, padding: '2px 8px' }}>
              Driver OK since {clock(incident.driver_ok_at)}
            </span>
          )}
          {incident.has_replayed && (
            <span className="pill pill--warn" style={{ fontSize: 10, padding: '2px 8px' }}>
              {REPLAYED_LABEL}
            </span>
          )}
          {closed && (
            <span className="pill" style={{ fontSize: 10, padding: '2px 8px' }}>
              Closed {clock(incident.closed_at)}
            </span>
          )}
        </div>
      )}
      {closed && incident.close_note && (
        <p style={{ margin: 0, fontSize: 11.5, color: 'var(--text-soft)' }}>
          “{incident.close_note}”
        </p>
      )}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {alertsNewestFirst.map((a) => (
          <AlertCard
            key={a.id_alert}
            alert={a}
            onShowOnMap={onShowOnMap}
            focused={a.id_alert === focusedAlertId}
          />
        ))}
      </div>
    </article>
  )
}
