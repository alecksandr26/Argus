import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import TravelManagement from './TravelManagement'
import { AuthProvider } from '../context/AuthContext'
import type { Driver, LoginUser, Route, Truck, User } from '../types'

const truck: Truck = {
  id_truck: 't-1',
  plate_number: 'ARG-001',
  brand: 'Kenworth',
  model: 'T680',
  company_number: 'TR-001',
  raspberry_pi_mac: null,
  esp32_id: null,
  operative_status: 'active',
  has_device_key: true,
  device_key_issued_at: null,
  device_last_seen_at: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

const driver: Driver = {
  id_driver: 'd-1',
  first_name: 'Ana',
  last_name: 'Ruiz',
  license_number: 'L-1',
  license_expiration: '2030-01-01',
  phone_number: '555',
  emergency_contact_name: 'B',
  emergency_contact_phone: '556',
  operative_status: 'resting',
  blood_type: 'O+',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

const route = (id: string, status: Route['operative_status']): Route => ({
  id_route: id,
  id_driver: 'd-1',
  id_truck: 't-1',
  origin_name: `From ${id}`,
  destination_name: `To ${id}`,
  destination_coordinates: { lat: 0, lon: 0 },
  estimated_departure: '2026-10-21T15:30:00Z',
  estimated_arrival: null,
  actual_departure: null,
  actual_arrival: null,
  operative_status: status,
  incident_state: 'fine',
  last_not_ok_at: null,
  stop_reason: null,
  stopped_by: null,
  stopped_at: null,
  id_guardian: 'g-1',
  guardian: { id_user: 'g-1', first_name: 'Gina', last_name: 'Guard', phone_number: '+523312345678' },
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
})

function seedSession(role: LoginUser['role']) {
  localStorage.setItem(
    'argus.session',
    JSON.stringify({
      token: 'tok',
      user: { id_user: 'u1', email: 'a@b.com', role, first_name: 'A', last_name: 'B' },
    }),
  )
}

const userRow = (over: Partial<User>): User => ({
  id_user: 'g-1',
  email: 'g1@argus.dev',
  role: 'guardian',
  first_name: 'Gina',
  last_name: 'Guard',
  phone_number: '+523312345678',
  is_active: true,
  last_login: null,
  ...over,
})

/** What a root_admin's `GET /api/users` returns: two usable guardians, one without an E.164
 * phone, one inactive, and a non-guardian. Only the first three may show in the select. */
const users: User[] = [
  userRow({}),
  userRow({ id_user: 'g-2', first_name: 'Ana', last_name: 'López', phone_number: '+525511112222' }),
  userRow({ id_user: 'g-3', first_name: 'Nora', last_name: 'Phoneless', phone_number: '555-0101' }),
  userRow({ id_user: 'g-4', first_name: 'Ida', last_name: 'Inactive', is_active: false }),
  userRow({ id_user: 'a-1', first_name: 'Adam', last_name: 'Admin', role: 'admin' }),
]

/** GETs answer from the fixtures; a PUT echoes the route back with the body merged in (and an
 * `id_guardian` resolved to its `guardian`, as the backend does, G4); a POST /stop returns it
 * cancelled with the reason; a POST /api/routes echoes the body as a new route. */
function mockBackend(routes: Route[]) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body })
    const resolve = (id: string | null) => {
      const u = users.find((x) => x.id_user === id)
      return u
        ? { id_user: u.id_user, first_name: u.first_name, last_name: u.last_name, phone_number: u.phone_number }
        : null
    }
    if (init?.method === 'POST' && url.endsWith('/api/routes')) {
      const body = JSON.parse(String(init.body))
      return ok({ ...route('r-new', 'scheduled'), ...body, guardian: resolve(body.id_guardian) })
    }
    if (init?.method === 'POST' && url.endsWith('/stop')) {
      const id = url.split('/').slice(-2)[0]
      const current = routes.find((r) => r.id_route === id)!
      const { reason } = JSON.parse(String(init.body))
      return ok({
        ...current,
        operative_status: 'cancelled',
        stop_reason: reason,
        stopped_by: 'u1',
        stopped_at: '2026-10-23T12:00:00Z',
      })
    }
    if (init?.method === 'PUT') {
      const id = url.split('/').pop()
      const current = routes.find((r) => r.id_route === id)!
      const body = JSON.parse(String(init.body))
      return ok({
        ...current,
        ...body,
        ...('id_guardian' in body ? { guardian: resolve(body.id_guardian) } : {}),
      })
    }
    if (url.includes('/api/drivers')) return ok([driver])
    if (url.includes('/api/trucks')) return ok([truck])
    if (url.includes('/api/users')) return ok(users)
    return ok(routes)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderPage() {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={['/routes']}>
        <TravelManagement />
      </MemoryRouter>
    </AuthProvider>,
  )
}

const putCalls = (fetchMock: ReturnType<typeof mockBackend>) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')
const postCalls = (fetchMock: ReturnType<typeof mockBackend>) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('TravelManagement trips', () => {
  it('Start trip sets the route in progress with a departure time', async () => {
    seedSession('admin')
    const fetchMock = mockBackend([route('r-1', 'scheduled')])
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'Start trip' }))

    const [[url, init]] = putCalls(fetchMock)
    expect(url).toContain('/api/routes/r-1')
    const body = JSON.parse(String(init!.body))
    expect(body.operative_status).toBe('in_progress')
    expect(Number.isNaN(Date.parse(body.actual_departure))).toBe(false)
    expect(await screen.findByRole('button', { name: 'Complete trip' })).toBeInTheDocument()
  })

  it('Complete trip asks for confirmation, then sets the route completed with an arrival time', async () => {
    seedSession('root_admin')
    const fetchMock = mockBackend([route('r-1', 'in_progress')])
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'Complete trip' }))
    const dialog = screen.getByRole('dialog', { name: 'Complete trip' })
    expect(putCalls(fetchMock)).toHaveLength(0)
    await user.click(within(dialog).getByRole('button', { name: 'Complete trip' }))

    const body = JSON.parse(String(putCalls(fetchMock)[0][1]!.body))
    expect(body.operative_status).toBe('completed')
    expect(Number.isNaN(Date.parse(body.actual_arrival))).toBe(false)
    expect(await screen.findByText('Completed')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Stop route' })).not.toBeInTheDocument()
  })

  it('Stop route needs a reason, then POSTs /stop and the route reads "Stopped"', async () => {
    seedSession('admin')
    const fetchMock = mockBackend([route('r-1', 'in_progress')])
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'Stop route' }))
    const dialog = screen.getByRole('dialog', { name: 'Stop route' })
    await user.click(within(dialog).getByRole('button', { name: 'Stop route' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent('A reason is required')
    expect(postCalls(fetchMock)).toHaveLength(0)

    await user.type(within(dialog).getByRole('textbox'), 'Truck broke down near Celaya')
    await user.click(within(dialog).getByRole('button', { name: 'Stop route' }))

    const [[url, init]] = postCalls(fetchMock)
    expect(url).toContain('/api/routes/r-1/stop')
    expect(JSON.parse(String(init!.body))).toEqual({ reason: 'Truck broke down near Celaya' })
    expect(await screen.findByText('Stopped')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Stop route' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Complete trip' })).not.toBeInTheDocument()
    // An ended route still opens in the route view (whole path, closed incidents).
    expect(screen.getByRole('link', { name: /View route/ })).toHaveAttribute('href', '/routes/r-1/live')
  })

  it('a scheduled route can be started or stopped, not completed', async () => {
    seedSession('root_admin')
    mockBackend([route('r-1', 'scheduled')])
    renderPage()
    expect(await screen.findByRole('button', { name: 'Start trip' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Stop route' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Complete trip' })).not.toBeInTheDocument()
  })

  it('completed and cancelled routes offer no trip buttons', async () => {
    seedSession('root_admin')
    mockBackend([route('r-1', 'completed'), route('r-2', 'cancelled')])
    renderPage()
    expect(await screen.findByText('From r-1 → To r-1')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Stop route' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Complete trip' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start trip' })).not.toBeInTheDocument()
  })

  it('refuses to start a second trip on a truck that already has one in progress', async () => {
    seedSession('admin')
    const fetchMock = mockBackend([route('r-1', 'in_progress'), route('r-2', 'scheduled')])
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'Start trip' }))

    expect(putCalls(fetchMock)).toHaveLength(0)
    expect(screen.getByRole('alert')).toHaveTextContent('ARG-001 already has a trip in progress')
  })

  it('guardian sees no trip buttons', async () => {
    seedSession('guardian')
    mockBackend([route('r-1', 'scheduled'), route('r-2', 'in_progress')])
    renderPage()

    expect(await screen.findByText('From r-1 → To r-1')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start trip' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Complete trip' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Stop route' })).not.toBeInTheDocument()
  })

  it('Stop route / Complete trip are disabled while a Start trip is in flight', async () => {
    seedSession('admin')
    const routes = [route('r-1', 'scheduled')]
    const inner = mockBackend(routes)
    let release!: () => void
    const held = new Promise<void>((r) => {
      release = r
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === 'PUT') await held
        return inner(url, init)
      }),
    )
    const user = userEvent.setup()
    renderPage()

    const stop = await screen.findByRole('button', { name: 'Stop route' })
    expect(stop).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Start trip' }))
    expect(screen.getByRole('button', { name: 'Start trip' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Stop route' })).toBeDisabled()

    release()
    expect(await screen.findByRole('button', { name: 'Complete trip' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Stop route' })).toBeEnabled()
  })
})

const unguarded = (id: string, status: Route['operative_status']): Route => ({
  ...route(id, status),
  id_guardian: null,
  guardian: null,
})

describe('TravelManagement guardians', () => {
  it('the Guardian select offers active guardians only, with a no-phone one disabled', async () => {
    seedSession('root_admin')
    mockBackend([])
    renderPage()

    const select = (await screen.findByLabelText('Guardian')) as HTMLSelectElement
    await waitFor(() => expect(select.options.length).toBeGreaterThan(1))
    const options = Array.from(select.options).map((o) => ({
      value: o.value,
      label: o.textContent,
      disabled: o.disabled,
    }))
    expect(options).toEqual([
      { value: '', label: 'Select a guardian', disabled: true },
      { value: 'g-2', label: 'Ana López · +525511112222', disabled: false },
      { value: 'g-1', label: 'Gina Guard · +523312345678', disabled: false },
      { value: 'g-3', label: 'Nora Phoneless (no valid phone)', disabled: true },
    ])
    expect(select.value).toBe('')
  })

  it('Create route needs a guardian, then POSTs its id_guardian', async () => {
    seedSession('admin')
    const fetchMock = mockBackend([])
    const user = userEvent.setup()
    renderPage()

    const select = await screen.findByLabelText('Guardian')
    await waitFor(() => expect(screen.getByRole('option', { name: /Ana López/ })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Create route' }))
    expect(screen.getByText('Select a guardian for this route')).toBeInTheDocument()
    expect(select).toHaveAttribute('aria-invalid', 'true')
    expect(postCalls(fetchMock)).toHaveLength(0)

    await user.selectOptions(select, 'g-2')
    expect(screen.queryByText('Select a guardian for this route')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Create route' }))

    const [[url, init]] = postCalls(fetchMock)
    expect(url).toMatch(/\/api\/routes$/)
    const body = JSON.parse(String(init!.body))
    expect(body.id_guardian).toBe('g-2')
    expect(body).not.toHaveProperty('guardian')
    // The new row shows its guardian, and the form resets to the placeholder.
    expect(await screen.findByText('Ana López')).toBeInTheDocument()
    expect((select as HTMLSelectElement).value).toBe('')
  })

  it('a guardian viewer sees each route’s embedded guardian without calling /api/users', async () => {
    seedSession('guardian')
    const fetchMock = mockBackend([route('r-1', 'in_progress'), unguarded('r-2', 'scheduled')])
    renderPage()

    expect(await screen.findByText('Gina Guard')).toBeInTheDocument()
    expect(screen.getByText('+523312345678')).toBeInTheDocument()
    expect(screen.getByText('No guardian')).toHaveClass('pill--warn')
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/api/users'))).toBe(false)
    expect(screen.queryByRole('button', { name: /guardian/ })).not.toBeInTheDocument()
  })

  it('warns when open routes have no guardian, and ignores ended ones', async () => {
    seedSession('root_admin')
    mockBackend([
      unguarded('r-1', 'scheduled'),
      unguarded('r-2', 'in_progress'),
      unguarded('r-3', 'completed'),
      route('r-4', 'in_progress'),
    ])
    renderPage()

    expect(await screen.findByTestId('no-guardian-banner')).toHaveTextContent(
      '2 active routes have no guardian — assign one so critical alerts are texted',
    )
  })

  it('no banner when every open route has a guardian', async () => {
    seedSession('root_admin')
    mockBackend([route('r-1', 'scheduled'), unguarded('r-2', 'cancelled')])
    renderPage()
    expect(await screen.findByText('From r-1 → To r-1')).toBeInTheDocument()
    expect(screen.queryByTestId('no-guardian-banner')).not.toBeInTheDocument()
  })

  it('Assign guardian PUTs id_guardian and the row and banner update', async () => {
    seedSession('admin')
    const fetchMock = mockBackend([unguarded('r-1', 'in_progress')])
    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByTestId('no-guardian-banner')).toHaveTextContent('1 active route has no guardian')
    await user.click(screen.getByRole('button', { name: 'Assign guardian' }))
    const dialog = screen.getByRole('dialog', { name: 'Assign guardian' })
    await user.click(within(dialog).getByRole('button', { name: 'Save guardian' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent('Select a guardian')
    expect(putCalls(fetchMock)).toHaveLength(0)

    await user.selectOptions(within(dialog).getByLabelText('Guardian'), 'g-1')
    await user.click(within(dialog).getByRole('button', { name: 'Save guardian' }))

    const [[url, init]] = putCalls(fetchMock)
    expect(url).toContain('/api/routes/r-1')
    expect(JSON.parse(String(init!.body))).toEqual({ id_guardian: 'g-1' })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByText('Gina Guard')).toBeInTheDocument()
    expect(screen.queryByTestId('no-guardian-banner')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Change guardian' })).toBeInTheDocument()
  })

  it('Assign guardian shows the backend’s 422/409 detail and keeps the dialog open', async () => {
    seedSession('root_admin')
    const inner = mockBackend([unguarded('r-1', 'scheduled')])
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === 'PUT') {
          return {
            ok: false,
            status: 422,
            json: async () => ({ detail: 'guardian has no valid E.164 phone' }),
          }
        }
        return inner(url, init)
      }),
    )
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'Assign guardian' }))
    const dialog = screen.getByRole('dialog', { name: 'Assign guardian' })
    await user.selectOptions(within(dialog).getByLabelText('Guardian'), 'g-2')
    await user.click(within(dialog).getByRole('button', { name: 'Save guardian' }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'guardian has no valid E.164 phone',
    )
    expect(screen.getByRole('dialog', { name: 'Assign guardian' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Save guardian' })).toBeEnabled()
  })

  it('ended routes offer no guardian action', async () => {
    seedSession('root_admin')
    mockBackend([unguarded('r-1', 'completed'), route('r-2', 'cancelled')])
    renderPage()
    expect(await screen.findByText('From r-1 → To r-1')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /guardian/ })).not.toBeInTheDocument()
  })
})
