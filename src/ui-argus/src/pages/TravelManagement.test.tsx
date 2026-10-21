import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
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

/** GETs answer from the fixtures; a PUT echoes the route back with the body merged in. */
function mockBackend(routes: Route[]) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body })
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

  it('Complete trip sets the route completed with an arrival time', async () => {
    seedSession('root_admin')
    const fetchMock = mockBackend([route('r-1', 'in_progress')])
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'Complete trip' }))

    const body = JSON.parse(String(putCalls(fetchMock)[0][1]!.body))
    expect(body.operative_status).toBe('completed')
    expect(Number.isNaN(Date.parse(body.actual_arrival))).toBe(false)
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
    mockBackend([route('r-1', 'scheduled')])
    renderPage()

    expect(await screen.findByText('From r-1 → To r-1')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start trip' })).not.toBeInTheDocument()
  })
})
