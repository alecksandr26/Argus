import { describe, it, expect } from 'vitest'
import {
  ALERT_TYPE_LABEL,
  alertTypeLabel,
  delivery,
  incidentState,
  incidentTier,
  isActiveRoute,
  REPLAYED_LABEL,
  routeStatus,
  routeStatusBadge,
  severity,
  truckStatus,
  driverStatus,
  vigilance,
} from './status'

describe('status maps', () => {
  it('cover every value with a label and a tone', () => {
    for (const map of [
      truckStatus,
      driverStatus,
      routeStatus,
      severity,
      vigilance,
      incidentState,
      incidentTier,
    ]) {
      for (const badge of Object.values(map)) {
        expect(badge.label).toBeTruthy()
        expect(['good', 'warn', 'bad', 'neutral']).toContain(badge.tone)
      }
    }
  })

  it('marks an active alert / critical status as the bad tone, not good', () => {
    expect(truckStatus.alert.tone).toBe('bad')
    expect(driverStatus.on_route_alert.tone).toBe('bad')
    expect(routeStatus.in_progress_alert.tone).toBe('bad')
    expect(severity.critical.tone).toBe('bad')
    expect(incidentState.critical.tone).toBe('bad')
    expect(incidentTier.critical.tone).toBe('bad')
  })

  it('colours the map marker states fine/medium/critical as good/warn/bad', () => {
    expect(incidentState.fine).toEqual({ label: 'Fine', tone: 'good' })
    expect(incidentState.medium.tone).toBe('warn')
    expect(incidentState.critical.tone).toBe('bad')
  })

  it('shows an unknown vigilance as neutral, not as Normal', () => {
    expect(vigilance.unknown).toEqual({ label: 'Unknown', tone: 'neutral' })
    expect(vigilance.low).toEqual(severity.low)
  })

  it('labels the Old tier', () => {
    expect(incidentTier.old.label).toBe('Older than 1 h')
  })
})

describe('alertTypeLabel', () => {
  it.each([
    ['eyes_closed', 'Eyes closed'],
    ['yawn_eyes_closed', 'Eyes closed after yawning'],
    ['head_down', 'Head down'],
    ['head_down_eyes_closed', 'Head down, eyes closed'],
    ['no_face', 'Driver not visible'],
    ['panic_button', 'Panic button'],
  ])('labels %s as "%s"', (type, label) => {
    expect(alertTypeLabel(type)).toBe(label)
    expect(ALERT_TYPE_LABEL[type]).toBe(label)
  })

  it('shows an unknown type as the raw string', () => {
    expect(alertTypeLabel('something_new')).toBe('something_new')
  })

  it('is not fooled by Object prototype keys', () => {
    expect(alertTypeLabel('toString')).toBe('toString')
  })
})

describe('delivery', () => {
  it('badges only a replayed (dlq) alert', () => {
    expect(delivery.live).toBeNull()
    expect(delivery.dlq).toEqual({ label: REPLAYED_LABEL, tone: 'warn' })
    expect(REPLAYED_LABEL).toBe('Recovered after lost connection')
  })
})

describe('routeStatusBadge', () => {
  it('reads a stopped route (cancelled with a reason) as "Stopped"', () => {
    expect(routeStatusBadge({ operative_status: 'cancelled', stop_reason: 'Breakdown' }).label).toBe(
      'Stopped',
    )
  })

  it('falls back to the plain status otherwise', () => {
    expect(routeStatusBadge({ operative_status: 'cancelled', stop_reason: null })).toEqual(
      routeStatus.cancelled,
    )
    expect(routeStatusBadge({ operative_status: 'in_progress', stop_reason: null })).toEqual(
      routeStatus.in_progress,
    )
  })
})

describe('isActiveRoute', () => {
  it('is true for in_progress and in_progress_alert', () => {
    expect(isActiveRoute('in_progress')).toBe(true)
    expect(isActiveRoute('in_progress_alert')).toBe(true)
  })

  it('is false for scheduled, completed, and cancelled', () => {
    expect(isActiveRoute('scheduled')).toBe(false)
    expect(isActiveRoute('completed')).toBe(false)
    expect(isActiveRoute('cancelled')).toBe(false)
  })
})
