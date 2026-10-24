import { describe, it, expect } from 'vitest'
import {
  relativeTime,
  clock,
  shortDate,
  pct,
  daysUntil,
  durationShort,
  coords,
  timeOfDay,
} from './format'

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

// All expected clock() times below are in America/Mexico_City (a fixed UTC-6 offset, no DST
// since Mexico's 2022 reform) — not the raw UTC hour of the ISO string, and not whatever
// timezone the test runner's own host happens to be in.
describe('clock', () => {
  it('renders "—" for a null timestamp', () => {
    expect(clock(null, NOW)).toBe('—')
  })

  it('renders a bare time for the same day, converted to Mexico City time', () => {
    // 06:30 UTC = 00:30 America/Mexico_City, same Mexico-City calendar day as NOW (06:00 local)
    expect(clock('2026-08-24T06:30:00.000Z', NOW)).toBe('00:30')
  })

  it('prefixes "Yesterday" for the previous day, converted to Mexico City time', () => {
    // 22:00 UTC = 16:00 America/Mexico_City the day before
    expect(clock('2026-08-23T22:00:00.000Z', NOW)).toBe('Yesterday 16:00')
  })

  it('falls back to a full date for anything older', () => {
    expect(clock('2026-08-20T22:00:00.000Z', NOW)).toBe(
      `${shortDate('2026-08-20T22:00:00.000Z')} 16:00`,
    )
  })

  it('uses the fixed America/Mexico_City offset, not the host machine timezone', () => {
    // 2026-08-25T05:00:00Z is a different UTC calendar day than NOW (2026-08-24), but the same
    // America/Mexico_City calendar day (2026-08-24T23:00 local) -- a host-timezone-naive
    // implementation (e.g. comparing raw UTC or the test runner's own local timezone) would
    // wrongly treat this as a different, older day instead of "today, 23:00".
    expect(clock('2026-08-25T05:00:00.000Z', NOW)).toBe('23:00')
  })
})

describe('millisecond timestamps (C3.3 [sync 3])', () => {
  it('clock and relativeTime read HH:MM:SS.mmmZ and longer fractions', () => {
    expect(clock('2026-08-24T06:30:07.481Z', NOW)).toBe('00:30')
    expect(clock('2026-08-24T06:30:07.481123Z', NOW)).toBe('00:30')
    expect(relativeTime('2026-08-24T11:59:40.999Z', NOW)).toBe('19s ago')
  })
})

describe('timeOfDay', () => {
  it('is always a bare Mexico City HH:MM, even for another day', () => {
    expect(timeOfDay('2026-08-24T06:30:07.481Z')).toBe('00:30')
    expect(timeOfDay('2026-08-20T22:00:00.000Z')).toBe('16:00')
    expect(timeOfDay('2026-08-24T20:32:00Z')).toMatch(/^\d{1,2}:\d{2}$/)
  })

  it('renders "—" for an unparseable time', () => {
    expect(timeOfDay('garbage')).toBe('—')
  })
})

describe('pct', () => {
  it('renders a 0-1 fraction as a rounded percentage', () => {
    expect(pct(0.7321)).toBe('73%')
    expect(pct(1)).toBe('100%')
    expect(pct(0)).toBe('0%')
  })
})

describe('durationShort', () => {
  it('renders seconds, minutes, hours and days compactly', () => {
    expect(durationShort(0)).toBe('0s')
    expect(durationShort(42.4)).toBe('42s')
    expect(durationShort(59.6)).toBe('1 min')
    expect(durationShort(180)).toBe('3 min')
    expect(durationShort(3600)).toBe('1h')
    expect(durationShort(3725)).toBe('1h 2m')
    expect(durationShort(86400)).toBe('1d')
    expect(durationShort(3 * 86400 + 4 * 3600 + 59)).toBe('3d 4h')
  })

  it('reads negative or non-finite input as 0s', () => {
    expect(durationShort(-5)).toBe('0s')
    expect(durationShort(Number.NaN)).toBe('0s')
    expect(durationShort(Number.POSITIVE_INFINITY)).toBe('0s')
  })
})

describe('coords', () => {
  it('renders lat, lon with 4 decimals', () => {
    expect(coords({ lat: 20.65971234, lon: -103.3 })).toBe('20.6597, -103.3000')
  })
})

describe('daysUntil', () => {
  it('is positive for a future date and negative for a past one', () => {
    expect(daysUntil('2026-08-27T12:00:00.000Z', NOW)).toBe(3)
    expect(daysUntil('2026-08-21T12:00:00.000Z', NOW)).toBe(-3)
  })
})
