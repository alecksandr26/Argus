import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
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

/** Routes fetch by URL + method, so a test can exercise the rotate/revoke calls. `rotateExtra`
 * is merged into the rotate-key answer (the G5 phone fields); without it the answer is the
 * pre-G5 shape. */
function mockFetchDevice(state: { truck: Truck; rotateExtra?: Record<string, unknown> }) {
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
      return json({
        id_truck: state.truck.id_truck,
        device_api_key: 'plain-key-123',
        ...state.rotateExtra,
      })
    }
    if (url.endsWith('/device-key') && method === 'DELETE') {
      state.truck = { ...state.truck, has_device_key: false, device_key_issued_at: null }
      return json(state.truck)
    }
    if (url.endsWith(`/api/trucks/${state.truck.id_truck}`) && method === 'PUT') {
      state.truck = { ...state.truck, ...JSON.parse(String(init?.body)) }
      return json(state.truck)
    }
    if (url.endsWith(`/api/trucks/${state.truck.id_truck}`)) return json(state.truck)
    throw new Error(`unexpected ${method} ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const rotateCalls = (fetchMock: ReturnType<typeof mockFetchDevice>) =>
  fetchMock.mock.calls.filter(
    ([url, init]) => String(url).endsWith('/rotate-key') && init?.method === 'POST',
  )

const keyedTruck: Truck = {
  ...truckRow,
  has_device_key: true,
  device_key_issued_at: '2026-01-01T00:00:00Z',
}

describe('Fleet device credentials', () => {
  beforeEach(() => {
    // Every confirm is drawn in place now; a native dialog would fail the test.
    vi.stubGlobal(
      'confirm',
      vi.fn(() => {
        throw new Error('window.confirm must not be used')
      }),
    )
    // Issuing starts a download; keep jsdom from trying to navigate to the blob URL.
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

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
    expect(screen.getByText('Device credentials ready for ARG-001')).toBeInTheDocument()
    expect(await screen.findByTestId('device-key-status')).toHaveTextContent(
      'device not seen yet',
    )
    expect(screen.getByRole('button', { name: 'Rotate key' })).toBeInTheDocument()
    expect(rotateCalls(fetchMock)).toHaveLength(1)
    // Fleet's rotate sends no route: the backend picks the truck's current or next one.
    expect(rotateCalls(fetchMock)[0][1]?.body).toBeUndefined()
  })

  it('rotating confirms in place and does nothing when cancelled', async () => {
    seedSession('admin')
    const fetchMock = mockFetchDevice({ truck: { ...keyedTruck } })
    const user = userEvent.setup()
    renderFleet()

    await user.click(await screen.findByText('ARG-001'))
    await user.click(screen.getByRole('button', { name: 'Rotate key' }))
    expect(screen.getByRole('group')).toHaveTextContent(
      "Rotate ARG-001's key? This disconnects the installed device",
    )
    await user.click(within(screen.getByRole('group')).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('group')).not.toBeInTheDocument()
    expect(screen.queryByTestId('device-env')).not.toBeInTheDocument()
    expect(rotateCalls(fetchMock)).toHaveLength(0)
  })

  it('rotating after the inline confirm issues a new key', async () => {
    seedSession('admin')
    const fetchMock = mockFetchDevice({ truck: { ...keyedTruck } })
    const user = userEvent.setup()
    renderFleet()

    await user.click(await screen.findByText('ARG-001'))
    await user.click(screen.getByRole('button', { name: 'Rotate key' }))
    expect(rotateCalls(fetchMock)).toHaveLength(0)
    await user.click(within(screen.getByRole('group')).getByRole('button', { name: 'Rotate key' }))
    expect(await screen.findByTestId('device-env')).toHaveTextContent(
      'ARGUS_DEVICE_API_KEY=plain-key-123',
    )
    expect(rotateCalls(fetchMock)).toHaveLength(1)
  })

  it('revoke confirms in place, then clears the key status', async () => {
    seedSession('root_admin')
    const fetchMock = mockFetchDevice({ truck: { ...keyedTruck } })
    const user = userEvent.setup()
    renderFleet()

    await user.click(await screen.findByText('ARG-001'))
    await user.click(screen.getByRole('button', { name: 'Revoke' }))
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false)
    await user.click(screen.getByRole('button', { name: 'Revoke key' }))
    expect(await screen.findByText('No credentials issued')).toBeInTheDocument()
  })

  it('guardian sees the key status but no credential buttons', async () => {
    seedSession('guardian')
    mockFetchDevice({ truck: { ...keyedTruck } })
    const user = userEvent.setup()
    renderFleet()

    await user.click(await screen.findByText('ARG-001'))
    expect(screen.getByTestId('device-key-status')).toHaveTextContent('Key issued')
    expect(screen.queryByRole('button', { name: 'Rotate key' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Revoke' })).not.toBeInTheDocument()
  })

  it('asks to save the truck first when the form has unsaved changes', async () => {
    seedSession('root_admin')
    const fetchMock = mockFetchDevice({ truck: { ...truckRow, alert_phone: null } })
    const user = userEvent.setup()
    renderFleet()

    await user.click(await screen.findByText('ARG-001'))
    expect(screen.queryByTestId('device-unsaved')).not.toBeInTheDocument()
    await user.type(screen.getByLabelText('SMS alert phone (optional)'), '+523312345678')
    expect(screen.getByRole('button', { name: 'Generate device credentials' })).toBeDisabled()
    expect(screen.getByTestId('device-unsaved')).toHaveTextContent(
      'Save the truck first, then rotate the key',
    )
    expect(rotateCalls(fetchMock)).toHaveLength(0)

    // Saving closes the form; reopening it, the key can be issued with the saved phone.
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(screen.queryByText('Edit truck')).not.toBeInTheDocument())
    await user.click(screen.getByText('ARG-001'))
    expect(screen.queryByTestId('device-unsaved')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Generate device credentials' }))
    expect(await screen.findByTestId('device-env')).toHaveTextContent(
      'ARGUS_ALERT_PHONE=+523312345678',
    )
  })

  describe('downloaded argus-device.env', () => {
    const { createObjectURL, revokeObjectURL } = URL
    let blobs: Blob[]
    let downloads: { name: string; attached: boolean }[]

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
        downloads.push({ name: this.download, attached: this.isConnected })
      })
    })

    afterEach(() => {
      URL.createObjectURL = createObjectURL
      URL.revokeObjectURL = revokeObjectURL
      vi.restoreAllMocks()
    })

    async function issueFor(truck: Truck, rotateExtra?: Record<string, unknown>) {
      seedSession('root_admin')
      const fetchMock = mockFetchDevice({ truck, rotateExtra })
      const user = userEvent.setup()
      renderFleet()

      await user.click(await screen.findByText(truck.plate_number))
      await user.click(screen.getByRole('button', { name: 'Generate device credentials' }))
      await screen.findByTestId('device-env')
      return { user, fetchMock }
    }

    async function downloadEnv(
      truck: Truck,
      rotateExtra?: Record<string, unknown>,
    ): Promise<string> {
      const { user } = await issueFor(truck, rotateExtra)
      await user.click(screen.getByRole('button', { name: /Download argus-device.env/ }))
      expect(downloads.map((d) => d.name)).toEqual(['argus-device.env', 'argus-device.env'])
      expect(blobs).toHaveLength(2)
      return blobs[1].text()
    }

    it('starts the download as soon as the key is issued', async () => {
      await issueFor({ ...truckRow })
      expect(downloads).toEqual([{ name: 'argus-device.env', attached: true }])
      expect(blobs[0].type).toBe('application/octet-stream')
      expect(await blobs[0].text()).toContain('ARGUS_DEVICE_API_KEY=plain-key-123')
      // The anchor is attached only while clicked; the URL is freed later, not in the same tick.
      expect(document.querySelectorAll('a[download]')).toHaveLength(0)
      expect(URL.revokeObjectURL).not.toHaveBeenCalled()
    })

    it('keeps the file after the truck form is saved, until dismissed', async () => {
      const { user } = await issueFor({ ...truckRow })
      await user.click(screen.getByRole('button', { name: 'Save changes' }))
      await waitFor(() => expect(screen.queryByText('Edit truck')).not.toBeInTheDocument())

      expect(screen.getByTestId('device-env')).toHaveTextContent(
        'ARGUS_DEVICE_API_KEY=plain-key-123',
      )
      await user.click(screen.getByRole('button', { name: /Download argus-device.env/ }))
      expect(downloads).toHaveLength(2)
      expect(await blobs[1].text()).toContain('ARGUS_DEVICE_API_KEY=plain-key-123')

      // Reopening the truck's form and cancelling it doesn't lose it either.
      await user.click(screen.getByText('ARG-001'))
      await user.click(screen.getByRole('button', { name: 'Cancel' }))
      expect(screen.getByTestId('device-env')).toBeInTheDocument()

      await user.click(screen.getByRole('button', { name: 'I saved the file — close' }))
      expect(screen.queryByTestId('device-env')).not.toBeInTheDocument()
    })

    it("uses the route guardian's phone from the rotate answer", async () => {
      const text = await downloadEnv(
        { ...truckRow, alert_phone: '+520000000001' },
        {
          alert_phone: '+523312345678',
          alert_phone_source: 'route_guardian',
          id_route: 'r-9',
          guardian_name: 'Ana López',
        },
      )
      expect(text.split('\n').slice(-5)).toEqual([
        'ARGUS_TRUCK_PLATE=ARG-001',
        '# SMS alert phone: guardian Ana López of route r-9',
        'ARGUS_ALERT_PHONE=+523312345678',
        'ARGUS_ALERT_PHONE_LABEL=guardian Ana López of route r-9',
        '',
      ])
      expect(text).toBe(screen.getByTestId('device-env').textContent)
      expect(screen.getByTestId('alert-phone-note')).toHaveTextContent(
        "Critical alerts are texted to guardian Ana López (+523312345678) of this truck's current route.",
      )
      expect(screen.getByTestId('alert-phone-note')).toHaveTextContent(
        'save that change first, then rotate the key',
      )
    })

    it("uses the truck's fallback phone from the rotate answer", async () => {
      const text = await downloadEnv(
        { ...truckRow, alert_phone: null },
        {
          alert_phone: '+523312345678',
          alert_phone_source: 'truck',
          id_route: null,
          guardian_name: null,
        },
      )
      expect(text.split('\n').slice(-5)).toEqual([
        'ARGUS_TRUCK_PLATE=ARG-001',
        "# SMS alert phone: the truck's fallback phone",
        'ARGUS_ALERT_PHONE=+523312345678',
        'ARGUS_ALERT_PHONE_LABEL=truck fallback phone',
        '',
      ])
      expect(screen.getByTestId('alert-phone-note')).toHaveTextContent(
        "Critical alerts are texted to the truck's fallback phone (+523312345678).",
      )
    })

    it('comments the phone out when the rotate answer has none', async () => {
      const text = await downloadEnv(
        { ...truckRow, alert_phone: '+520000000001' },
        { alert_phone: null, alert_phone_source: null, id_route: null, guardian_name: null },
      )
      expect(text.split('\n').slice(-3)).toEqual([
        'ARGUS_TRUCK_PLATE=ARG-001',
        '# ARGUS_ALERT_PHONE=  (no SMS alert phone set for this truck)',
        '',
      ])
      expect(screen.getByTestId('alert-phone-note')).toHaveTextContent(
        "No SMS phone: assign a guardian to the truck's route or set the truck's alert phone.",
      )
    })

    it("falls back to the truck's phone when the backend predates G5", async () => {
      const text = await downloadEnv({ ...truckRow, alert_phone: '+523312345678' })
      expect(text.split('\n').slice(-5)).toEqual([
        'ARGUS_TRUCK_PLATE=ARG-001',
        "# SMS alert phone: the truck's fallback phone",
        'ARGUS_ALERT_PHONE=+523312345678',
        'ARGUS_ALERT_PHONE_LABEL=truck fallback phone',
        '',
      ])
    })
  })
})
