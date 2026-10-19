import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import Fleet from './Fleet'
import { AuthProvider } from '../context/AuthContext'
import type { LoginUser, Truck } from '../types'

const truckRow: Truck = {
  id_truck: 't-1',
  plate_number: 'ARG-001',
  brand: 'Kenworth',
  model: 'T680',
  company_number: 'TR-001',
  raspberry_pi_mac: null,
  esp32_id: null,
  operative_status: 'active',
  has_device_key: false,
  device_key_issued_at: null,
  device_last_seen_at: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

function seedSession(role: LoginUser['role']) {
  localStorage.setItem(
    'argus.session',
    JSON.stringify({
      token: 'tok',
      user: { id_user: 'u1', email: 'a@b.com', role, first_name: 'A', last_name: 'B' },
    }),
  )
}

function mockFetchList() {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => [truckRow] }),
  )
}

function renderFleet() {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={['/fleet']}>
        <Fleet />
      </MemoryRouter>
    </AuthProvider>,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Fleet write-gating', () => {
  it('root_admin sees "Add truck" and can edit an existing row', async () => {
    seedSession('root_admin')
    mockFetchList()
    const user = userEvent.setup()
    renderFleet()

    expect(screen.getByRole('button', { name: /Add truck/ })).toBeInTheDocument()
    await user.click(await screen.findByText('ARG-001'))
    expect(screen.getByText('Edit truck')).toBeInTheDocument()
    expect(screen.getByLabelText('Plate')).not.toBeDisabled()
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeInTheDocument()
  })

  it('guardian has no "Add truck" button and sees rows read-only', async () => {
    seedSession('guardian')
    mockFetchList()
    const user = userEvent.setup()
    renderFleet()

    expect(screen.queryByRole('button', { name: /Add truck/ })).not.toBeInTheDocument()
    await user.click(await screen.findByText('ARG-001'))
    expect(screen.getByText('View truck')).toBeInTheDocument()
    expect(screen.getByLabelText('Plate')).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Save changes' })).not.toBeInTheDocument()
    // Two elements share the accessible name "Close" here (the icon-only dismiss button's
    // aria-label, and this panel's text button) — assert on the text node specifically.
    expect(screen.getByText('Close')).toBeInTheDocument()
  })
})

/** Routes fetch by URL + method, so a test can exercise the rotate/revoke calls. */
function mockFetchDevice(state: { truck: Truck }) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    const json = (body: unknown) => ({ ok: true, status: 200, json: async () => body })
    if (url.endsWith('/api/trucks') && method === 'GET') return json([state.truck])
    if (url.endsWith('/rotate-key') && method === 'POST') {
      state.truck = {
        ...state.truck,
        has_device_key: true,
        device_key_issued_at: new Date().toISOString(),
      }
      return json({ id_truck: state.truck.id_truck, device_api_key: 'plain-key-123' })
    }
    if (url.endsWith('/device-key') && method === 'DELETE') {
      state.truck = { ...state.truck, has_device_key: false, device_key_issued_at: null }
      return json(state.truck)
    }
    if (url.endsWith(`/api/trucks/${state.truck.id_truck}`)) return json(state.truck)
    throw new Error(`unexpected ${method} ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('Fleet device credentials', () => {
  it('root_admin generates credentials and sees the env file once', async () => {
    seedSession('root_admin')
    const fetchMock = mockFetchDevice({ truck: { ...truckRow } })
    const user = userEvent.setup()
    renderFleet()

    await user.click(await screen.findByText('ARG-001'))
    expect(screen.getByTestId('device-key-status')).toHaveTextContent('No credentials issued')
    await user.click(screen.getByRole('button', { name: 'Generate device credentials' }))

    const env = await screen.findByTestId('device-env')
    expect(env).toHaveTextContent('ARGUS_TRUCK_ID=t-1')
    expect(env).toHaveTextContent('ARGUS_DEVICE_API_KEY=plain-key-123')
    expect(env).toHaveTextContent('ARGUS_BACKEND_URL=')
    expect(screen.getByRole('button', { name: /Download argus-device.env/ })).toBeInTheDocument()
    expect(screen.getByTestId('device-key-status')).toHaveTextContent('device not seen yet')
    expect(screen.getByRole('button', { name: 'Rotate key' })).toBeInTheDocument()
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => String(url).endsWith('/rotate-key') && init?.method === 'POST',
      ),
    ).toBe(true)
  })

  it('rotating asks for confirmation and does nothing when declined', async () => {
    seedSession('admin')
    const fetchMock = mockFetchDevice({
      truck: { ...truckRow, has_device_key: true, device_key_issued_at: '2026-01-01T00:00:00Z' },
    })
    const confirm = vi.fn().mockReturnValue(false)
    vi.stubGlobal('confirm', confirm)
    const user = userEvent.setup()
    renderFleet()

    await user.click(await screen.findByText('ARG-001'))
    await user.click(screen.getByRole('button', { name: 'Rotate key' }))
    expect(confirm).toHaveBeenCalled()
    expect(screen.queryByTestId('device-env')).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/rotate-key'))).toBe(false)
  })

  it('revoke clears the key status', async () => {
    seedSession('root_admin')
    mockFetchDevice({
      truck: { ...truckRow, has_device_key: true, device_key_issued_at: '2026-01-01T00:00:00Z' },
    })
    vi.stubGlobal('confirm', vi.fn().mockReturnValue(true))
    const user = userEvent.setup()
    renderFleet()

    await user.click(await screen.findByText('ARG-001'))
    await user.click(screen.getByRole('button', { name: 'Revoke' }))
    expect(await screen.findByText('No credentials issued')).toBeInTheDocument()
  })

  it('guardian sees the key status but no credential buttons', async () => {
    seedSession('guardian')
    mockFetchDevice({
      truck: { ...truckRow, has_device_key: true, device_key_issued_at: '2026-01-01T00:00:00Z' },
    })
    const user = userEvent.setup()
    renderFleet()

    await user.click(await screen.findByText('ARG-001'))
    expect(screen.getByTestId('device-key-status')).toHaveTextContent('Key issued')
    expect(screen.queryByRole('button', { name: 'Rotate key' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Revoke' })).not.toBeInTheDocument()
  })
})
