import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import LiveOps from './LiveOps'
import { AuthProvider } from '../context/AuthContext'
import { at, makeAlert, makeIncident, makeLive, makePoint, makeRouteLive } from '../test/fixtures'
import type { FleetMapMarker, MapFocus, MapPath, MapPin } from '../components/FleetMap'
import type { LiveResponse, LoginUser } from '../types'

// Leaflet doesn't run in jsdom; capture what the page hands the map instead.
const map = vi.hoisted(() => ({
  props: null as null | {
    markers: FleetMapMarker[]
    paths?: MapPath[]
    pins?: MapPin[]
    focus?: MapFocus | null
  },
}))
vi.mock('../components/FleetMap', () => ({
  default: (props: typeof map.props) => {
    map.props = props
    return <div data-testid="fleet-map" />
  },
}))

function seedSession(role: LoginUser['role']) {
  localStorage.setItem(
    'argus.session',
    JSON.stringify({
      token: 'tok',
      user: { id_user: 'u1', email: 'a@b.com', role, first_name: 'A', last_name: 'B' },
    }),
  )
}

function ok(body: unknown) {
  return { ok: true, status: 200, json: async () => body }
}

const position = (over = {}) => ({
  t: at(10),
  lat: 20.7,
  lon: -103.4,
  speed: 70,
  vigilance: 'low' as const,
  last_known: false,
  location_age_s: 0,
  ...over,
})

function liveFixture(): LiveResponse {
  return makeLive({
    routes: [
      makeRouteLive({
        id_route: 'r-1',
        incident_state: 'critical',
        latest_position: position(),
        truck_plate_number: 'ARG-001',
      }),
      makeRouteLive({
        id_route: 'r-2',
        incident_state: 'fine',
        latest_position: position({ last_known: true, location_age_s: 120 }),
        truck_plate_number: 'ARG-002',
        driver_full_name: 'Luis Mora',
      }),
    ],
    incidents: [
      makeIncident({
        id_incident: 'i-c',
        id_route: 'r-1',
        severity: 'critical',
        tier: 'critical',
        alerts: [
          makeAlert({
            id_alert: 'a-c',
            alert_type: 'no_face',
            severity_level: 'critical',
            coordinates: { lat: 20.5, lon: -103.1 },
          }),
        ],
      }),
      makeIncident({
        id_incident: 'i-m',
        id_route: 'r-2',
        tier: 'medium',
        truck_plate_number: 'ARG-002',
        driver_full_name: 'Luis Mora',
        alerts: [makeAlert({ id_alert: 'a-m', id_route: 'r-2', alert_type: 'yawn_eyes_closed' })],
      }),
    ],
    track: [makePoint({ t: at(0) }), makePoint({ t: at(5), lat: 20.66 })],
  })
}

function mockBackend(live: LiveResponse = liveFixture()) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const u = new URL(url)
    if (u.pathname === '/api/live') return ok(live)
    const close = u.pathname.match(/^\/api\/incidents\/([^/]+)\/close$/)
    if (close && init?.method === 'POST') {
      const inc = live.incidents.find((i) => i.id_incident === close[1])!
      const note = JSON.parse(String(init.body)).note
      return ok({ ...inc, closed_at: '2026-10-23T20:32:41.123Z', closed_by: 'u1', close_note: note })
    }
    if (u.pathname.endsWith('/stop') && init?.method === 'POST') {
      return ok({ ...live.routes[0], operative_status: 'cancelled' })
    }
    throw new Error(`unexpected ${init?.method ?? 'GET'} ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderPage() {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={['/']}>
        <LiveOps />
      </MemoryRouter>
    </AuthProvider>,
  )
}

beforeEach(() => {
  map.props = null
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('LiveOps', () => {
  it('polls only /api/live (no /routes/active or /alerts) and renders the tiered feed', async () => {
    seedSession('guardian')
    const fetchMock = mockBackend()
    renderPage()

    expect(await screen.findByRole('region', { name: 'Critical incidents' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Medium incidents' })).toBeInTheDocument()
    expect(screen.getByText('Driver not visible')).toBeInTheDocument()
    expect(screen.getByText('Eyes closed after yawning')).toBeInTheDocument()
    const paths = fetchMock.mock.calls.map(([u]) => new URL(u).pathname)
    expect(new Set(paths)).toEqual(new Set(['/api/live']))
  })

  it('hands the map markers keyed by route and coloured by incident_state, paths and pins', async () => {
    seedSession('guardian')
    mockBackend()
    renderPage()

    await waitFor(() => expect(map.props?.markers).toHaveLength(2))
    const [m1, m2] = map.props!.markers
    expect(m1).toMatchObject({ id: 'r-1', tone: 'bad', lastKnown: false })
    expect(m2).toMatchObject({
      id: 'r-2',
      tone: 'good',
      lastKnown: true,
      positionLabel: 'Last known (2 min old)',
    })
    expect(map.props!.paths).toEqual([
      {
        id: 'r-1',
        positions: [
          [20.6597, -103.3496],
          [20.66, -103.3496],
        ],
      },
    ])
    expect(map.props!.pins!.map((p) => p.id)).toEqual(['a-c', 'a-m'])
  })

  it('stat tiles count routes by incident_state', async () => {
    seedSession('guardian')
    mockBackend()
    renderPage()
    await screen.findByRole('region', { name: 'Critical incidents' })
    const tile = (label: string) =>
      [...document.querySelectorAll('.stat-card')].find(
        (el) => el.querySelector('.stat-card__label')?.textContent === label,
      )!
    expect(tile('Trucks on route')).toHaveTextContent('2')
    expect(tile('Normal')).toHaveTextContent('1')
    expect(tile('Medium severity')).toHaveTextContent('0')
    expect(tile('Critical severity')).toHaveTextContent('1')
  })

  it('"Show on map" focuses the map on that alert', async () => {
    seedSession('guardian')
    mockBackend()
    const user = userEvent.setup()
    renderPage()

    const critical = await screen.findByRole('region', { name: 'Critical incidents' })
    await user.click(within(critical).getByRole('button', { name: 'Show on map' }))
    expect(map.props!.focus).toMatchObject({ position: [20.5, -103.1], pinId: 'a-c' })
  })

  it('tier chips filter the feed', async () => {
    seedSession('guardian')
    mockBackend()
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('region', { name: 'Critical incidents' })

    await user.click(screen.getByRole('button', { name: 'Medium' }))
    expect(screen.queryByRole('region', { name: 'Critical incidents' })).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Medium incidents' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Old' }))
    expect(screen.getByText('No open incidents in this tier.')).toBeInTheDocument()
  })

  it('guardian can close incidents but not end routes', async () => {
    seedSession('guardian')
    mockBackend()
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('region', { name: 'Critical incidents' })
    expect(screen.getAllByRole('button', { name: 'Close incident' })).toHaveLength(2)

    await user.click(screen.getByRole('button', { name: 'Routes on the road' }))
    expect(screen.getAllByTestId('active-route')).toHaveLength(2)
    expect(screen.queryByRole('button', { name: 'Stop route' })).not.toBeInTheDocument()
  })

  it('admin stops a route from the routes panel (reason required), then re-polls', async () => {
    seedSession('admin')
    const fetchMock = mockBackend()
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('region', { name: 'Critical incidents' })
    expect(screen.queryByRole('button', { name: 'Close incident' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Routes on the road' }))
    const first = screen.getAllByTestId('active-route')[0]
    expect(within(first).getByText('20.7000, -103.4000')).toBeInTheDocument()
    expect(within(screen.getAllByTestId('active-route')[1]).getByText(/Last known \(2 min old\)/))
      .toBeInTheDocument()

    await user.click(within(first).getByRole('button', { name: 'Stop route' }))
    const dialog = screen.getByRole('dialog', { name: 'Stop route' })
    await user.type(within(dialog).getByRole('textbox'), 'Driver relieved')
    const before = fetchMock.mock.calls.length
    await user.click(within(dialog).getByRole('button', { name: 'Stop route' }))

    const stop = fetchMock.mock.calls.find(([u]) => String(u).endsWith('/api/routes/r-1/stop'))!
    expect(JSON.parse(String(stop[1]!.body))).toEqual({ reason: 'Driver relieved' })
    await waitFor(() =>
      expect(
        fetchMock.mock.calls
          .slice(before)
          .filter(([u]) => new URL(u).pathname === '/api/live').length,
      ).toBeGreaterThan(0),
    )
  })

  it('after a close: a role=status "Closed HH:MM" confirmation, the incident leaves the feed, no panel switch', async () => {
    seedSession('guardian')
    // The backend keeps returning the incident (a poll that was in flight): it stays hidden.
    const fetchMock = mockBackend()
    const user = userEvent.setup()
    renderPage()

    const medium = await screen.findByRole('region', { name: 'Medium incidents' })
    expect(screen.getByRole('status')).toBeEmptyDOMElement()
    const card = within(medium).getByRole('article', { name: 'Medium incident' })
    await user.click(within(card).getByRole('button', { name: 'Close incident' }))
    const dialog = screen.getByRole('dialog', { name: 'Close incident' })
    await user.click(within(dialog).getByRole('button', { name: 'Close incident' }))

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/Closed \d{1,2}:\d{2}/))
    // 20:32:41.123Z is 14:32 in Mexico City.
    expect(screen.getByRole('status')).toHaveTextContent('Medium incident closed · ARG-002 · Closed 14:32')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Medium incidents' })).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Critical incidents' })).toBeInTheDocument()
    expect(screen.getByText('1 open')).toBeInTheDocument()
    expect(map.props!.pins!.map((p) => p.id)).toEqual(['a-c'])
    // Still on the incidents panel, all tiers (the view doesn't switch on its own).
    expect(screen.getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'true')

    // It re-polled after the close, and the confirmation stays (no auto-dismiss).
    await waitFor(() =>
      expect(fetchMock.mock.calls.filter(([u]) => new URL(u).pathname === '/api/live').length).toBeGreaterThan(1),
    )
    expect(screen.getByRole('status')).toHaveTextContent('Closed 14:32')
  })
})
