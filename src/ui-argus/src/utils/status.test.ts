import { describe, it, expect } from 'vitest'
import { isActiveRoute, truckStatus, driverStatus, routeStatus, vigilance, severity } from './status'

describe('status maps', () => {
  it('cover every value with a label and a tone', () => {
    for (const map of [truckStatus, driverStatus, routeStatus, vigilance, severity]) {
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
    expect(vigilance.critical.tone).toBe('bad')
    expect(severity.critical.tone).toBe('bad')
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
