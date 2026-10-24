import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
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

/** Answers the list GET and echoes a POST/PUT body back as the saved truck. */
function mockFetchSave() {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    const json = (body: unknown) => ({ ok: true, status: 200, json: async () => body })
    if (url.endsWith('/api/trucks') && method === 'GET') return json([truckRow])
    if (method === 'POST' || method === 'PUT') {
      return json({ ...truckRow, ...JSON.parse(String(init?.body)) })
    }
    throw new Error(`unexpected ${method} ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function sentBody(fetchMock: ReturnType<typeof mockFetchSave>, method: string) {
  const call = fetchMock.mock.calls.find(([, init]) => init?.method === method)
  return call ? JSON.parse(String(call[1]?.body)) : undefined
}

describe('Fleet SMS alert phone', () => {
  it('creates a truck with the phone', async () => {
    seedSession('admin')
    const fetchMock = mockFetchSave()
    const user = userEvent.setup()
    renderFleet()

    await user.click(screen.getByRole('button', { name: /Add truck/ }))
    await user.type(screen.getByLabelText('Plate'), 'ARG-009')
    const phone = screen.getByLabelText('SMS alert phone (optional)')
    expect(phone).toHaveAttribute('placeholder', '+523312345678')
    await user.type(phone, '+523312345678')
    await user.click(screen.getByRole('button', { name: 'Create truck' }))

    await screen.findAllByText('ARG-009')
    expect(sentBody(fetchMock, 'POST')).toMatchObject({
      plate_number: 'ARG-009',
      alert_phone: '+523312345678',
    })
  })

  it('creates a truck without a phone as null', async () => {
    seedSession('admin')
    const fetchMock = mockFetchSave()
    const user = userEvent.setup()
    renderFleet()

    await user.click(screen.getByRole('button', { name: /Add truck/ }))
    await user.type(screen.getByLabelText('Plate'), 'ARG-010')
    await user.click(screen.getByRole('button', { name: 'Create truck' }))

    await screen.findAllByText('ARG-010')
    expect(sentBody(fetchMock, 'POST').alert_phone).toBeNull()
  })

  it('edits the phone, and clearing it sends null', async () => {
    seedSession('root_admin')
    const fetchMock = mockFetchSave()
    const user = userEvent.setup()
    renderFleet()

    await user.click(await screen.findByText('ARG-001'))
    await user.type(screen.getByLabelText('SMS alert phone (optional)'), ' +5215512345678 ')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(screen.queryByText('Edit truck')).not.toBeInTheDocument())
    expect(sentBody(fetchMock, 'PUT').alert_phone).toBe('+5215512345678')

    fetchMock.mockClear()
    await user.click(screen.getByText('ARG-001'))
    const phone = screen.getByLabelText('SMS alert phone (optional)')
    expect(phone).toHaveValue('+5215512345678')
    await user.clear(phone)
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(screen.queryByText('Edit truck')).not.toBeInTheDocument())
    expect(sentBody(fetchMock, 'PUT').alert_phone).toBeNull()
  })

  it('rejects a phone that is not E.164 without calling the backend', async () => {
    seedSession('root_admin')
    const fetchMock = mockFetchSave()
    const user = userEvent.setup()
    renderFleet()

    await user.click(await screen.findByText('ARG-001'))
    const phone = screen.getByLabelText('SMS alert phone (optional)')
    await user.type(phone, '33 1234 5678')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Use the international format, e.g. +523312345678',
    )
    expect(phone).toHaveAttribute('aria-invalid', 'true')
    expect(phone).toHaveAccessibleDescription(
      'Use the international format, e.g. +523312345678',
    )
    expect(sentBody(fetchMock, 'PUT')).toBeUndefined()

    // Editing the field clears the error.
    await user.type(phone, '9')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('is read-only for a guardian', async () => {
    seedSession('guardian')
    mockFetchList()
    const user = userEvent.setup()
    renderFleet()

    await user.click(await screen.findByText('ARG-001'))
    expect(screen.getByLabelText('SMS alert phone (optional)')).toBeDisabled()
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

  describe('downloaded argus-device.env', () => {
    const { createObjectURL, revokeObjectURL } = URL
    let blobs: Blob[]
    let downloads: string[]

    beforeEach(() => {
      blobs = []
      downloads = []
      // jsdom has neither; the component only needs a URL back and the anchor not to navigate.
      URL.createObjectURL = vi.fn((b: Blob) => {
        blobs.push(b)
        return 'blob:argus-test'
      })
      URL.revokeObjectURL = vi.fn()
      vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
        this: HTMLAnchorElement,
      ) {
        downloads.push(this.download)
      })
    })

    afterEach(() => {
      URL.createObjectURL = createObjectURL
      URL.revokeObjectURL = revokeObjectURL
      vi.restoreAllMocks()
    })

    async function downloadEnv(truck: Truck): Promise<string> {
      seedSession('root_admin')
      mockFetchDevice({ truck })
      const user = userEvent.setup()
      renderFleet()

      await user.click(await screen.findByText(truck.plate_number))
      await user.click(screen.getByRole('button', { name: 'Generate device credentials' }))
      await user.click(await screen.findByRole('button', { name: /Download argus-device.env/ }))
      expect(downloads).toEqual(['argus-device.env'])
      expect(blobs).toHaveLength(1)
      return blobs[0].text()
    }

    it('carries the plate and the truck phone', async () => {
      const text = await downloadEnv({ ...truckRow, alert_phone: '+523312345678' })
      const lines = text.split('\n')
      expect(lines).toContain('ARGUS_TRUCK_ID=t-1')
      expect(lines).toContain('ARGUS_DEVICE_API_KEY=plain-key-123')
      expect(lines.slice(-3)).toEqual([
        'ARGUS_TRUCK_PLATE=ARG-001',
        'ARGUS_ALERT_PHONE=+523312345678',
        '',
      ])
      expect(text).toBe(screen.getByTestId('device-env').textContent)
      expect(screen.getByTestId('alert-phone-note')).toHaveTextContent(
        'Critical alerts are texted to +523312345678.',
      )
      expect(screen.getByTestId('alert-phone-note')).toHaveTextContent(
        'if you change it later, generate new credentials',
      )
    })

    it('comments the phone out when the truck has none', async () => {
      const text = await downloadEnv({ ...truckRow, alert_phone: null })
      expect(text.split('\n').slice(-3)).toEqual([
        'ARGUS_TRUCK_PLATE=ARG-001',
        '# ARGUS_ALERT_PHONE=  (no SMS alert phone set for this truck)',
        '',
      ])
      expect(screen.getByTestId('alert-phone-note')).toHaveTextContent(
        'No SMS alert phone is set',
      )
    })

    it('warns that a phone typed but not saved is not in the file', async () => {
      seedSession('root_admin')
      mockFetchDevice({ truck: { ...truckRow, alert_phone: null } })
      const user = userEvent.setup()
      renderFleet()

      await user.click(await screen.findByText('ARG-001'))
      await user.type(screen.getByLabelText('SMS alert phone (optional)'), '+523312345678')
      await user.click(screen.getByRole('button', { name: 'Generate device credentials' }))

      const env = await screen.findByTestId('device-env')
      expect(env.textContent).toContain(
        '# ARGUS_ALERT_PHONE=  (no SMS alert phone set for this truck)',
      )
      expect(env.textContent).not.toContain('ARGUS_ALERT_PHONE=+523312345678')
      expect(screen.getByTestId('alert-phone-unsaved')).toHaveTextContent("isn't saved")
    })

    it('shows no unsaved warning when the form matches the saved phone', async () => {
      await downloadEnv({ ...truckRow, alert_phone: '+523312345678' })
      expect(screen.queryByTestId('alert-phone-unsaved')).not.toBeInTheDocument()
    })
  })
})
