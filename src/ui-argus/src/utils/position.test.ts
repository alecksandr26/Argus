import { describe, it, expect } from 'vitest'
import { alertPosition, lastKnownLabel, pointPosition, positionSummary } from './position'
import { makeAlert, makePoint } from '../test/fixtures'

describe('alertPosition', () => {
  it('has no label for a live fix', () => {
    const p = alertPosition(makeAlert())
    expect(p).toEqual({
      kind: 'live',
      coordinates: { lat: 20.6597, lon: -103.3496 },
      ageSeconds: null,
      label: null,
    })
  })

  it('labels the last known fix with its age', () => {
    const p = alertPosition(makeAlert({ location_is_last_known: true, location_age_s: 180 }))
    expect(p.kind).toBe('last_known')
    expect(p.coordinates).toEqual({ lat: 20.6597, lon: -103.3496 })
    expect(p.label).toBe('Last known (3 min old)')
  })

  it('says "Position unknown" when there are no coordinates', () => {
    const p = alertPosition(makeAlert({ coordinates: null, position_unknown: true }))
    expect(p).toEqual({ kind: 'unknown', coordinates: null, ageSeconds: null, label: 'Position unknown' })
  })

  it('trusts position_unknown over stray coordinates', () => {
    expect(alertPosition(makeAlert({ position_unknown: true })).kind).toBe('unknown')
  })
})

describe('pointPosition', () => {
  it('reads a GPS point', () => {
    expect(pointPosition(makePoint()).kind).toBe('live')
    const lk = pointPosition(makePoint({ last_known: true, location_age_s: 3725 }))
    expect(lk.label).toBe('Last known (1h 2m old)')
  })
})

describe('lastKnownLabel', () => {
  it('says the age is unknown when the device sent none', () => {
    expect(lastKnownLabel(null)).toBe('Last known (age unknown)')
  })

  it('formats seconds', () => {
    expect(lastKnownLabel(42)).toBe('Last known (42s old)')
  })
})

describe('positionSummary', () => {
  it('is the coordinates for a live fix', () => {
    expect(positionSummary(alertPosition(makeAlert()))).toBe('20.6597, -103.3496')
  })

  it('adds the last-known label', () => {
    const p = alertPosition(makeAlert({ location_is_last_known: true, location_age_s: 60 }))
    expect(positionSummary(p)).toBe('20.6597, -103.3496 · Last known (1 min old)')
  })

  it('is "Position unknown" without coordinates', () => {
    const p = alertPosition(makeAlert({ coordinates: null, position_unknown: true }))
    expect(positionSummary(p)).toBe('Position unknown')
  })
})
