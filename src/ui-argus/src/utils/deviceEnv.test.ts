import { describe, it, expect } from 'vitest'
import { buildDeviceEnv, isLoopbackUrl, normalizeBackendUrl } from './deviceEnv'

describe('buildDeviceEnv', () => {
  const env = buildDeviceEnv({
    truckId: 't-1',
    plate: 'ARG-001',
    deviceApiKey: 'secret-key',
    backendUrl: 'https://demo.example.com/',
    issuedAt: new Date('2026-10-18T12:00:00Z'),
  })

  it('carries the three variables cv-argus reads', () => {
    const vars = env.split('\n').filter((l) => l && !l.startsWith('#'))
    expect(vars).toEqual([
      'ARGUS_TRUCK_ID=t-1',
      'ARGUS_DEVICE_API_KEY=secret-key',
      'ARGUS_BACKEND_URL=https://demo.example.com',
    ])
  })

  it('names the truck and issue time in its header comment', () => {
    expect(env).toContain('truck ARG-001')
    expect(env).toContain('2026-10-18T12:00:00.000Z')
  })
})

describe('normalizeBackendUrl', () => {
  it('drops trailing slashes and whitespace', () => {
    expect(normalizeBackendUrl(' http://10.0.0.5:8000// ')).toBe('http://10.0.0.5:8000')
  })
})

describe('isLoopbackUrl', () => {
  it.each([
    ['http://localhost:8000', true],
    ['http://127.0.0.1:8000', true],
    ['https://d123.cloudfront.net', false],
    ['http://192.168.1.20:8000', false],
    ['not a url', false],
  ])('%s -> %s', (url, expected) => {
    expect(isLoopbackUrl(url)).toBe(expected)
  })
})
