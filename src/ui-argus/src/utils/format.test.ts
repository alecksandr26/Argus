import { describe, it, expect } from 'vitest'
import { relativeTime, clock, shortDate, pct, daysUntil } from './format'

const NOW = new Date('2026-08-24T12:00:00.000Z')

describe('relativeTime', () => {
  it('renders whole seconds under a minute', () => {
    expect(relativeTime('2026-08-24T11:59:40.000Z', NOW)).toBe('20s ago')
  })

  it('renders whole minutes under an hour', () => {
    expect(relativeTime('2026-08-24T11:55:00.000Z', NOW)).toBe('5 min ago')
  })

  it('renders hours, with a remainder in minutes', () => {
    expect(relativeTime('2026-08-24T09:30:00.000Z', NOW)).toBe('2h 30m ago')
  })

  it('drops the remainder when it is exactly on the hour', () => {
    expect(relativeTime('2026-08-24T10:00:00.000Z', NOW)).toBe('2h ago')
  })

  it('renders days once past 24h', () => {
    expect(relativeTime('2026-08-21T12:00:00.000Z', NOW)).toBe('3d ago')
  })

  it('reports a future timestamp explicitly rather than a negative duration', () => {
    expect(relativeTime('2026-08-24T12:05:00.000Z', NOW)).toBe('in the future')
  })
})

describe('clock', () => {
  it('renders "—" for a null timestamp', () => {
    expect(clock(null, NOW)).toBe('—')
  })

  it('renders a bare time for the same day', () => {
    expect(clock('2026-08-24T06:30:00.000Z', NOW)).toBe('06:30')
  })

  it('prefixes "Yesterday" for the previous day', () => {
    expect(clock('2026-08-23T22:00:00.000Z', NOW)).toBe('Yesterday 22:00')
  })

  it('falls back to a full date for anything older', () => {
    expect(clock('2026-08-20T22:00:00.000Z', NOW)).toBe(
      `${shortDate('2026-08-20T22:00:00.000Z')} 22:00`,
    )
  })
})

describe('pct', () => {
  it('renders a 0-1 fraction as a rounded percentage', () => {
    expect(pct(0.7321)).toBe('73%')
    expect(pct(1)).toBe('100%')
    expect(pct(0)).toBe('0%')
  })
})

describe('daysUntil', () => {
  it('is positive for a future date and negative for a past one', () => {
    expect(daysUntil('2026-08-27T12:00:00.000Z', NOW)).toBe(3)
    expect(daysUntil('2026-08-21T12:00:00.000Z', NOW)).toBe(-3)
  })
})
