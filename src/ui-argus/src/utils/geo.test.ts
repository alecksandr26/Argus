import { describe, it, expect } from 'vitest'
import { normalizeCoordinates, toLatLng } from './geo'

describe('normalizeCoordinates', () => {
  it('passes through the canonical { lat, lon } shape', () => {
    expect(normalizeCoordinates({ lat: 20.58, lon: -100.38 })).toEqual({
      lat: 20.58,
      lon: -100.38,
    })
  })

  it('accepts the Leaflet { lat, lng } spelling', () => {
    expect(normalizeCoordinates({ lat: 20.58, lng: -100.38 })).toEqual({
      lat: 20.58,
      lon: -100.38,
    })
  })

  it('accepts a bare GeoJSON position ([lng, lat], longitude first)', () => {
    expect(normalizeCoordinates([-100.38, 20.58])).toEqual({
      lat: 20.58,
      lon: -100.38,
    })
  })

  it('drops a GeoJSON position elevation third element', () => {
    expect(normalizeCoordinates([-100.38, 20.58, 1200])).toEqual({
      lat: 20.58,
      lon: -100.38,
    })
  })

  it('accepts a GeoJSON Point geometry, the shape a Mongo 2dsphere index stores', () => {
    expect(
      normalizeCoordinates({ type: 'Point', coordinates: [-100.38, 20.58] }),
    ).toEqual({ lat: 20.58, lon: -100.38 })
  })

  it('accepts a "lat,lon" string, trimming whitespace', () => {
    expect(normalizeCoordinates('20.58, -100.38')).toEqual({
      lat: 20.58,
      lon: -100.38,
    })
  })

  it('throws on an unrecognised shape', () => {
    expect(() => normalizeCoordinates({ foo: 'bar' })).toThrow(
      /unrecognised coordinate shape/,
    )
    expect(() => normalizeCoordinates(42)).toThrow()
    expect(() => normalizeCoordinates(null)).toThrow()
  })

  it('throws on an out-of-range value rather than silently accepting it', () => {
    // A classic swapped [lat, lon] vs [lng, lat] bug: 200 isn't a valid latitude.
    expect(() => normalizeCoordinates({ lat: 200, lon: -100.38 })).toThrow(
      /out-of-range/,
    )
    expect(() => normalizeCoordinates([-100.38, 200])).toThrow(/out-of-range/)
  })

  it('throws on a malformed GeoJSON position (wrong length or non-numeric)', () => {
    expect(() =>
      normalizeCoordinates({ type: 'Point', coordinates: [1] }),
    ).toThrow(/expected a GeoJSON position/)
    expect(() =>
      normalizeCoordinates({ type: 'Point', coordinates: ['a', 'b'] }),
    ).toThrow(/must be two numbers/)
  })
})

describe('toLatLng', () => {
  it("converts the canonical shape to Leaflet's [lat, lng] tuple", () => {
    expect(toLatLng({ lat: 20.58, lon: -100.38 })).toEqual([20.58, -100.38])
  })
})
