import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import TravelManagement from './TravelManagement'
import { AuthProvider } from '../context/AuthContext'
import type { Driver, LoginUser, Route, Truck } from '../types'

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

/** GETs answer from the fixtures; a PUT echoes the route back with the body merged in; a
 * POST /stop returns it cancelled with the reason. */
function mockBackend(routes: Route[]) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body })
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
      return ok({ ...current, ...JSON.parse(String(init.body)) })
    }
    if (url.includes('/api/drivers')) return ok([driver])
    if (url.includes('/api/trucks')) return ok([truck])
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
})
