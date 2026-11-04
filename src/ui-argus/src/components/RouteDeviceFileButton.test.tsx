import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import RouteDeviceFileButton from './RouteDeviceFileButton'
import type { Route, Truck } from '../types'

const truck: Truck = {
  id_truck: 't-1',
  plate_number: 'ARG-001',
  brand: 'Kenworth',
  model: 'T680',
  company_number: 'TR-001',
  raspberry_pi_mac: null,
  esp32_id: null,
  alert_phone: '+520000000001',
  operative_status: 'active',
  has_device_key: true,
  device_key_issued_at: '2026-01-01T00:00:00Z',
  device_last_seen_at: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

const route: Route = {
  id_route: 'r-1',
  id_driver: 'd-1',
  id_truck: 't-1',
  origin_name: 'Guadalajara',
  destination_name: 'CDMX',
  destination_coordinates: { lat: 19.43, lon: -99.13 },
  estimated_departure: '2026-11-03T08:00:00Z',
  estimated_arrival: null,
  actual_departure: null,
  actual_arrival: null,
  operative_status: 'scheduled',
  incident_state: 'fine',
  last_not_ok_at: null,
  stop_reason: null,
  stopped_by: null,
  stopped_at: null,
  id_guardian: 'g-1',
  guardian: { id_user: 'g-1', first_name: 'Ana', last_name: 'López', phone_number: '+523312345678' },
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

function mockFetch(rotateAnswer: Record<string, unknown>) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const json = (body: unknown) => ({ ok: true, status: 200, json: async () => body })
    if (url.endsWith('/api/trucks/t-1/rotate-key') && init?.method === 'POST') {
      return json({ id_truck: 't-1', device_api_key: 'route-key-1', ...rotateAnswer })
    }
    if (url.endsWith('/api/trucks/t-1')) return json({ ...truck, device_key_issued_at: 'now' })
    throw new Error(`unexpected ${init?.method ?? 'GET'} ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const guardianAnswer = {
  alert_phone: '+523312345678',
  alert_phone_source: 'route_guardian',
  id_route: 'r-1',
  guardian_name: 'Ana López',
}

const { createObjectURL, revokeObjectURL } = URL
let blobs: Blob[]

beforeEach(() => {
  blobs = []
  URL.createObjectURL = vi.fn((b: Blob) => {
    blobs.push(b)
    return 'blob:argus-test'
  })
  URL.revokeObjectURL = vi.fn()
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  vi.stubGlobal(
    'confirm',
    vi.fn(() => {
      throw new Error('window.confirm must not be used')
    }),
  )
})

afterEach(() => {
  URL.createObjectURL = createObjectURL
  URL.revokeObjectURL = revokeObjectURL
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('RouteDeviceFileButton', () => {
  it('rotates with the route id after the inline confirm and downloads at once', async () => {
    const fetchMock = mockFetch(guardianAnswer)
    const onTruckChange = vi.fn()
    const user = userEvent.setup()
    render(
      <RouteDeviceFileButton route={route} truck={truck} token="tok" onTruckChange={onTruckChange} />,
    )

    await user.click(screen.getByRole('button', { name: 'Device file' }))
    expect(screen.getByRole('group')).toHaveTextContent(
      "Rotate ARG-001's key for route Guadalajara → CDMX? This disconnects the installed device",
    )
    expect(fetchMock).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Rotate key' }))

    const dialog = await screen.findByRole('dialog', { name: 'Device file for ARG-001' })
    const rotate = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/rotate-key'))!
    expect(JSON.parse(String(rotate[1]?.body))).toEqual({ id_route: 'r-1' })
    expect(rotate[1]?.headers).toMatchObject({ Authorization: 'Bearer tok' })

    // The download already started, with the guardian's phone.
    expect(blobs).toHaveLength(1)
    const text = await blobs[0].text()
    expect(text).toContain('# SMS alert phone: guardian Ana López of route r-1')
    expect(text).toContain(
      'ARGUS_ALERT_PHONE=+523312345678\nARGUS_ALERT_PHONE_LABEL=guardian Ana López of route r-1\n',
    )
    expect(text).not.toContain('+520000000001')

    expect(within(dialog).getByTestId('alert-phone-note')).toHaveTextContent(
      'Critical alerts are texted to guardian Ana López (+523312345678) of route Guadalajara → CDMX.',
    )
    expect(within(dialog).getByTestId('device-env')).toHaveTextContent(
      'ARGUS_DEVICE_API_KEY=route-key-1',
    )
    expect(onTruckChange).toHaveBeenCalledWith(expect.objectContaining({ id_truck: 't-1' }))

    // Escape doesn't drop the only copy of the key; the explicit button does.
    await user.keyboard('{Escape}')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: /Download argus-device.env/ }))
    expect(blobs).toHaveLength(2)
    await user.click(within(dialog).getByRole('button', { name: 'I saved the file — close' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Device file' })).toBeInTheDocument()
  })

  it('does nothing when the confirm is cancelled', async () => {
    const fetchMock = mockFetch(guardianAnswer)
    const user = userEvent.setup()
    render(<RouteDeviceFileButton route={route} truck={truck} token="tok" />)

    await user.click(screen.getByRole('button', { name: 'Device file' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(fetchMock).not.toHaveBeenCalled()
    expect(blobs).toHaveLength(0)
    expect(screen.getByRole('button', { name: 'Device file' })).toBeInTheDocument()
  })

  it("names the truck's fallback phone when the route has no usable guardian", async () => {
    mockFetch({
      alert_phone: '+520000000001',
      alert_phone_source: 'truck',
      id_route: 'r-1',
      guardian_name: null,
    })
    const user = userEvent.setup()
    render(<RouteDeviceFileButton route={route} truck={{ ...truck, has_device_key: false }} token="tok" />)

    await user.click(screen.getByRole('button', { name: 'Device file' }))
    expect(screen.getByRole('group')).toHaveTextContent(
      "Issue ARG-001's device credentials for route Guadalajara → CDMX?",
    )
    await user.click(screen.getByRole('button', { name: 'Issue file' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByTestId('alert-phone-note')).toHaveTextContent(
      "Critical alerts are texted to the truck's fallback phone (+520000000001).",
    )
    expect(await blobs[0].text()).toContain("# SMS alert phone: the truck's fallback phone")
  })

  it('shows the backend error and keeps the confirm open', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: async () => ({ detail: 'id_route not found for this truck' }),
      }),
    )
    const user = userEvent.setup()
    render(<RouteDeviceFileButton route={route} truck={truck} token="tok" />)

    await user.click(screen.getByRole('button', { name: 'Device file' }))
    await user.click(screen.getByRole('button', { name: 'Rotate key' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('id_route not found for this truck')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(blobs).toHaveLength(0)
  })
})
