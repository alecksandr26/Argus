import { describe, it, expect } from 'vitest'
import { buildDeviceEnv, isLoopbackUrl, normalizeBackendUrl } from './deviceEnv'

describe('buildDeviceEnv', () => {
  const input = {
    truckId: 't-1',
    plate: 'ARG-001',
    deviceApiKey: 'secret-key',
    backendUrl: 'https://demo.example.com/',
    issuedAt: new Date('2026-10-18T12:00:00Z'),
  }
  const env = buildDeviceEnv(input)
  const head = [
    '# Argus edge-device credentials — truck ARG-001',
    '# Issued 2026-10-18T12:00:00.000Z. Shown once: keep this file secret, never commit it.',
    "# Place it next to src/cv-argus/docker-compose.yml on the truck's Raspberry Pi as",
    '# argus-device.env, then restart the container (docker compose up -d).',
    'ARGUS_TRUCK_ID=t-1',
    'ARGUS_DEVICE_API_KEY=secret-key',
    'ARGUS_BACKEND_URL=https://demo.example.com',
    'ARGUS_TRUCK_PLATE=ARG-001',
  ]

  it('adds the plate and the alert phone after the three credentials', () => {
    expect(buildDeviceEnv({ ...input, alertPhone: '+523312345678' })).toBe(
      [...head, 'ARGUS_ALERT_PHONE=+523312345678', ''].join('\n'),
    )
  })

  it('comments the phone line out when the truck has no phone', () => {
    const expected = [
      ...head,
      '# ARGUS_ALERT_PHONE=  (no SMS alert phone set for this truck)',
      '',
    ].join('\n')
    expect(env).toBe(expected)
    expect(buildDeviceEnv({ ...input, alertPhone: null })).toBe(expected)
    expect(buildDeviceEnv({ ...input, alertPhone: '  ' })).toBe(expected)
  })

  it('carries the variables cv-argus reads', () => {
    const vars = env.split('\n').filter((l) => l && !l.startsWith('#'))
    expect(vars).toEqual([
      'ARGUS_TRUCK_ID=t-1',
      'ARGUS_DEVICE_API_KEY=secret-key',
      'ARGUS_BACKEND_URL=https://demo.example.com',
      'ARGUS_TRUCK_PLATE=ARG-001',
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
