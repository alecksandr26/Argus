import { describe, it, expect } from 'vitest'
import { buildDeviceEnv, isLoopbackUrl, issuedPhone, normalizeBackendUrl } from './deviceEnv'

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

  it("names a route guardian's phone source (G7)", () => {
    expect(
      buildDeviceEnv({
        ...input,
        alertPhone: '+523312345678',
        alertPhoneSource: 'route_guardian',
        guardianName: 'Ana López',
        idRoute: 'r-1',
      }),
    ).toBe(
      [
        ...head,
        '# SMS alert phone: guardian Ana López of route r-1',
        'ARGUS_ALERT_PHONE=+523312345678',
        'ARGUS_ALERT_PHONE_LABEL=guardian Ana López of route r-1',
        '',
      ].join('\n'),
    )
  })

  it("names the truck's fallback phone source (G7)", () => {
    expect(
      buildDeviceEnv({ ...input, alertPhone: '+523312345678', alertPhoneSource: 'truck' }),
    ).toBe(
      [
        ...head,
        "# SMS alert phone: the truck's fallback phone",
        'ARGUS_ALERT_PHONE=+523312345678',
        'ARGUS_ALERT_PHONE_LABEL=truck fallback phone',
        '',
      ].join('\n'),
    )
  })

  it('writes no source or label line when there is no phone', () => {
    const text = buildDeviceEnv({
      ...input,
      alertPhone: null,
      alertPhoneSource: 'route_guardian',
      guardianName: 'Ana López',
      idRoute: 'r-1',
    })
    expect(text).not.toContain('# SMS alert phone:')
    expect(text).not.toContain('ARGUS_ALERT_PHONE_LABEL')
  })

  it('writes no label for a phone without a known source', () => {
    expect(buildDeviceEnv({ ...input, alertPhone: '+523312345678' })).not.toContain(
      'ARGUS_ALERT_PHONE_LABEL',
    )
  })

  it('keeps the label on one line, without control characters', () => {
    const text = buildDeviceEnv({
      ...input,
      alertPhone: '+523312345678',
      alertPhoneSource: 'route_guardian',
      guardianName: 'Ana\nARGUS_DEVICE_API_KEY=evil\r\tLópez\u0000\u2028x',
      idRoute: 'r-1',
    })
    const lines = text.split('\n')
    expect(lines.filter((l) => l.startsWith('ARGUS_DEVICE_API_KEY='))).toEqual([
      'ARGUS_DEVICE_API_KEY=secret-key',
    ])
    expect(lines).toContain(
      'ARGUS_ALERT_PHONE_LABEL=guardian Ana ARGUS_DEVICE_API_KEY=evil López x of route r-1',
    )
    expect(lines).toContain(
      '# SMS alert phone: guardian Ana ARGUS_DEVICE_API_KEY=evil López x of route r-1',
    )
    // eslint-disable-next-line no-control-regex
    expect(text.replace(/\n/g, '')).not.toMatch(/[\u0000-\u001f\u007f\u2028\u2029]/)
  })

  it('caps the label value at 120 characters', () => {
    const text = buildDeviceEnv({
      ...input,
      alertPhone: '+523312345678',
      alertPhoneSource: 'route_guardian',
      guardianName: 'A'.repeat(300),
      idRoute: 'r-1',
    })
    const label = text.split('\n').find((l) => l.startsWith('ARGUS_ALERT_PHONE_LABEL='))!
    expect(label.slice('ARGUS_ALERT_PHONE_LABEL='.length)).toHaveLength(120)
    expect(label).toBe(`ARGUS_ALERT_PHONE_LABEL=guardian ${'A'.repeat(111)}`)
  })
})

describe('issuedPhone', () => {
  const key = { id_truck: 't-1', device_api_key: 'k' }

  it("takes the rotate answer's phone over the truck's", () => {
    expect(
      issuedPhone(
        {
          ...key,
          alert_phone: '+523312345678',
          alert_phone_source: 'route_guardian',
          id_route: 'r-1',
          guardian_name: 'Ana López',
        },
        '+520000000001',
      ),
    ).toEqual({
      phone: '+523312345678',
      source: 'route_guardian',
      guardianName: 'Ana López',
      idRoute: 'r-1',
    })
  })

  it('keeps a null answer null, even when the truck has a phone', () => {
    expect(
      issuedPhone(
        { ...key, alert_phone: null, alert_phone_source: null, id_route: null, guardian_name: null },
        '+520000000001',
      ),
    ).toEqual({ phone: null, source: null, guardianName: null, idRoute: null })
  })

  it("falls back to the truck's phone for a pre-G5 answer", () => {
    expect(issuedPhone(key, '+520000000001')).toEqual({
      phone: '+520000000001',
      source: 'truck',
      guardianName: null,
      idRoute: null,
    })
    expect(issuedPhone(key, null).phone).toBeNull()
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
