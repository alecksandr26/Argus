import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route as RouterRoute, Routes } from 'react-router-dom'
import RouteLiveView from './RouteLiveView'
import { AuthProvider } from '../context/AuthContext'
import { UI_POLL_MS } from '../constants'
import { at, makeAlert, makeIncident, makeLive, makePoint, makeRouteLive } from '../test/fixtures'
import type { FleetMapMarker, MapFocus, MapPath, MapPin } from '../components/FleetMap'
import type { Alert, Incident, LiveResponse, LoginUser, RouteLive, TrackPoint } from '../types'

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

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body })
const notFound = () => ({ ok: false, status: 404, json: async () => ({ detail: 'Not found' }) })

interface Backend {
  route?: RouteLive | null
  /** One `/api/live` response per poll (the last one repeats). */
  lives?: Partial<LiveResponse>[]
  track?: TrackPoint[]
  closed?: Incident[]
  alert?: Alert
}

function mockBackend(b: Backend = {}) {
  const route = b.route === undefined ? makeRouteLive({ id_route: 'r-1' }) : b.route
  let poll = 0
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const u = new URL(url)
    const method = init?.method ?? 'GET'
    if (u.pathname === '/api/live') {
      if (!route) return notFound()
      const lives = b.lives ?? [{}]
      const res = lives[Math.min(poll, lives.length - 1)]
      poll += 1
      return ok(makeLive({ routes: [route], ...res }))
    }
    if (u.pathname === '/api/routes/r-1/track') {
      return ok({ id_route: 'r-1', downsampled: false, points: b.track ?? [] })
    }
    if (u.pathname === '/api/incidents' && method === 'GET') return ok(b.closed ?? [])
    if (u.pathname.startsWith('/api/incidents/') && u.pathname.endsWith('/close')) {
      return ok({ ...makeIncident(), closed_at: at(100) })
    }
    if (u.pathname.startsWith('/api/alerts/') && b.alert) return ok(b.alert)
    if (u.pathname.endsWith('/stop') && method === 'POST') {
      return ok({ ...route, operative_status: 'cancelled', stop_reason: 'x' })
    }
    throw new Error(`unexpected ${method} ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderAt(path: string) {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <RouterRoute path="/routes/:routeId/live" element={<RouteLiveView />} />
          <RouterRoute path="/alerts/:alertId" element={<RouteLiveView />} />
        </Routes>
      </MemoryRouter>
    </AuthProvider>,
  )
}

const pathTimes = () => map.props?.paths?.[0]?.positions.map((p) => p[0]) ?? []

beforeEach(() => {
  map.props = null
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('RouteLiveView', () => {
  it('draws the whole path: seeded from /track, then the /api/live deltas folded in', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    seedSession('guardian')
    // Each point's latitude encodes its time so the drawn order is easy to read back.
    const pt = (s: number) => makePoint({ t: at(s), lat: 20 + s / 10000 })
    const fetchMock = mockBackend({
      track: [pt(0), pt(60), pt(3590)],
      lives: [
        { server_time: at(3600), track: [pt(3590)] },
        { server_time: at(3605), track: [pt(3590), pt(3601)] },
      ],
    })
    renderAt('/routes/r-1/live')

    await waitFor(() => expect(pathTimes()).toEqual([20, 20.006, 20.359]))
    await act(async () => {
      vi.advanceTimersByTime(UI_POLL_MS)
    })
    await waitFor(() => expect(pathTimes()).toEqual([20, 20.006, 20.359, 20.3601]))

    const calls = fetchMock.mock.calls.map(([u]) => new URL(u))
    expect(calls.filter((u) => u.pathname.endsWith('/track'))).toHaveLength(1)
    const lives = calls.filter((u) => u.pathname === '/api/live')
    expect(lives[0].searchParams.get('route')).toBe('r-1')
    expect(lives[1].searchParams.get('since')).toBe(at(3600))
  })

  it('also seeds the path of an ended route', async () => {
    seedSession('guardian')
    mockBackend({
      route: makeRouteLive({ id_route: 'r-1', operative_status: 'completed' }),
      track: [makePoint({ t: at(0) }), makePoint({ t: at(5), lat: 20.7 })],
      lives: [{ track: [] }],
    })
    renderAt('/routes/r-1/live')
    await waitFor(() => expect(map.props?.paths?.[0]?.positions).toHaveLength(2))
  })

  it('the marker is coloured by incident_state and shows the last-known position', async () => {
    seedSession('guardian')
    mockBackend({
      route: makeRouteLive({
        id_route: 'r-1',
        incident_state: 'medium',
        latest_position: {
          t: at(0),
          lat: 20.5,
          lon: -103,
          speed: 0,
          vigilance: 'unknown',
          last_known: true,
          location_age_s: 600,
        },
      }),
    })
    renderAt('/routes/r-1/live')
    await waitFor(() => expect(map.props?.markers).toHaveLength(1))
    expect(map.props!.markers[0]).toMatchObject({ id: 'r-1', tone: 'warn', lastKnown: true })
    expect(screen.getByText('20.5000, -103.0000 · Last known (10 min old)')).toBeInTheDocument()
  })

  it('shows the open incidents of the route, and the closed ones on demand', async () => {
    seedSession('guardian')
    mockBackend({
      lives: [
        {
          incidents: [
            makeIncident({
              severity: 'critical',
              tier: 'critical',
              alerts: [makeAlert({ alert_type: 'head_down_eyes_closed', severity_level: 'critical' })],
            }),
          ],
        },
      ],
      closed: [
        makeIncident({
          id_incident: 'i-old',
          closed_at: at(50),
          close_note: 'False positive, sunglasses',
          alerts: [makeAlert({ id_alert: 'a-old', alert_type: 'eyes_closed' })],
        }),
      ],
    })
    const user = userEvent.setup()
    renderAt('/routes/r-1/live')

    const critical = await screen.findByRole('region', { name: 'Critical incidents' })
    const link = within(critical).getByRole('link', { name: 'Head down, eyes closed' })
    expect(link).toHaveAttribute('href', '/routes/r-1/live?alert=a-1')

    await user.click(screen.getByRole('button', { name: 'Closed' }))
    expect(await screen.findByText('“False positive, sunglasses”')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Close incident' })).not.toBeInTheDocument()
    await waitFor(() => expect(map.props!.pins!.map((p) => p.id).sort()).toEqual(['a-1', 'a-old']))
  })

  it('focused alert: no grip or panic UI, position with Show on map, close its incident with a note', async () => {
    seedSession('root_admin')
    const alert = makeAlert({
      id_alert: 'a-1',
      severity_level: 'critical',
      alert_type: 'no_face',
      grip_status: 'bad',
      delivery: 'dlq',
      driver_ok_at: '2026-10-23T12:03:00Z',
      ai_metadata: { model: 'tiny_yolo_w1' },
    })
    const incident = makeIncident({ severity: 'critical', tier: 'critical', alerts: [alert] })
    const fetchMock = mockBackend({ alert, lives: [{ incidents: [incident] }] })
    const user = userEvent.setup()
    renderAt('/routes/r-1/live?alert=a-1')

    expect(await screen.findByRole('heading', { name: 'Driver not visible' })).toBeInTheDocument()
    expect(screen.getByText('CRITICAL SEVERITY')).toBeInTheDocument()
    expect(screen.queryByText(/drowsy/i)).not.toBeInTheDocument()
    expect(screen.queryByText('Immediate actions')).not.toBeInTheDocument()
    expect(screen.queryByText('Steering-wheel grip')).not.toBeInTheDocument()
    expect(screen.queryByText(/grip sensor/)).not.toBeInTheDocument()
    expect(screen.queryByText(/panic/i)).not.toBeInTheDocument()
    expect(screen.getAllByText('Recovered after lost connection').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/^Driver OK since/).length).toBeGreaterThan(0)
    expect(screen.getByTestId('focused-alert-position')).toHaveTextContent('20.6597, -103.3496')

    const panel = screen.getByTestId('focused-alert-position').parentElement!
    await user.click(within(panel).getByRole('button', { name: 'Show on map' }))
    expect(map.props!.focus).toMatchObject({ position: [20.6597, -103.3496], pinId: 'a-1' })

    await user.click(screen.getByRole('button', { name: 'Close incident' }))
    const dialog = screen.getByRole('dialog', { name: 'Close incident' })
    await user.click(within(dialog).getByRole('button', { name: 'Close incident' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent('A note is required')
    await user.type(within(dialog).getByRole('textbox'), 'Driver pulled over')
    await user.click(within(dialog).getByRole('button', { name: 'Close incident' }))

    const close = fetchMock.mock.calls.find(([u]) => String(u).endsWith('/api/incidents/i-1/close'))!
    expect(JSON.parse(String(close[1]!.body))).toEqual({ note: 'Driver pulled over' })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    // C3.7: the confirmation, and the focused alert's incident is no longer offered for closing.
    expect(screen.getByRole('status')).toHaveTextContent(/Closed \d{1,2}:\d{2}/)
    expect(screen.queryByRole('button', { name: 'Close incident' })).not.toBeInTheDocument()
    // The view does not switch tabs on its own: still on "This alert".
    expect(screen.getByRole('button', { name: 'This alert' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: /^Incidents/ })).toHaveAttribute('aria-pressed', 'false')
  })

  it('close from the Open feed with no focused alert: role=status "Closed HH:MM", leaves Open, stays on Open', async () => {
    seedSession('guardian')
    const incident = makeIncident({
      tier: 'medium',
      alerts: [makeAlert({ alert_type: 'yawn_eyes_closed' })],
    })
    // The live poll keeps returning the incident (as a poll in flight would): it stays hidden.
    const fetchMock = mockBackend({ lives: [{ incidents: [incident] }] })
    const user = userEvent.setup()
    renderAt('/routes/r-1/live')

    const medium = await screen.findByRole('region', { name: 'Medium incidents' })
    expect(screen.getByRole('status')).toBeEmptyDOMElement()
    const card = within(medium).getByRole('article', { name: 'Medium incident' })
    expect(within(card).getByText('Eyes closed after yawning')).toBeInTheDocument()
    await user.click(within(card).getByRole('button', { name: 'Close incident' }))
    const dialog = screen.getByRole('dialog', { name: 'Close incident' })
    await user.click(within(dialog).getByRole('button', { name: 'Close incident' }))

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/Closed \d{1,2}:\d{2}/))
    // at(100) = 12:01:40Z = 06:01 in Mexico City.
    expect(screen.getByRole('status')).toHaveTextContent('Medium incident closed · ARG-001 · Closed 06:01')
    expect(screen.queryByRole('region', { name: 'Medium incidents' })).not.toBeInTheDocument()
    expect(screen.getByText('No open incidents on this route.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Incidents (0 open)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Closed' })).toHaveAttribute('aria-pressed', 'false')
    // It re-read the closed list for the Closed chip.
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([u]) => new URL(u).pathname === '/api/incidents').length,
      ).toBeGreaterThan(1),
    )
  })

  it('"Follow truck" clears a "Show on map" focus so the map follows the truck again', async () => {
    seedSession('guardian')
    mockBackend({
      route: makeRouteLive({
        id_route: 'r-1',
        latest_position: {
          t: at(0),
          lat: 20.5,
          lon: -103,
          speed: 60,
          vigilance: 'low',
          last_known: false,
          location_age_s: 0,
        },
      }),
      lives: [{ incidents: [makeIncident()] }],
    })
    const user = userEvent.setup()
    renderAt('/routes/r-1/live')

    const medium = await screen.findByRole('region', { name: 'Medium incidents' })
    expect(screen.queryByRole('button', { name: 'Follow truck' })).not.toBeInTheDocument()
    await user.click(within(medium).getByRole('button', { name: 'Show on map' }))
    expect(map.props!.focus).toMatchObject({ pinId: 'a-1' })

    await user.click(screen.getByRole('button', { name: 'Follow truck' }))
    expect(map.props!.focus).toBeNull()
    expect(screen.queryByRole('button', { name: 'Follow truck' })).not.toBeInTheDocument()
  })

  it('an old panic-button row still reads, labelled "Panic button"', async () => {
    seedSession('guardian')
    const alert = makeAlert({ alert_type: 'panic_button', source: 'panic_button', ai_metadata: null })
    mockBackend({ alert })
    renderAt('/alerts/a-1')
    expect(await screen.findByRole('heading', { name: 'Panic button' })).toBeInTheDocument()
  })

  it('admin sees Complete trip and Stop route on an active route', async () => {
    seedSession('admin')
    mockBackend()
    renderAt('/routes/r-1/live')
    expect(await screen.findByRole('button', { name: 'Stop route' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Complete trip' })).toBeInTheDocument()
  })

  it('a stopped route shows its reason and no end buttons', async () => {
    seedSession('root_admin')
    mockBackend({
      route: makeRouteLive({
        id_route: 'r-1',
        operative_status: 'cancelled',
        stop_reason: 'Truck broke down',
        stopped_at: at(30),
      }),
    })
    renderAt('/routes/r-1/live')
    expect(await screen.findByTestId('stop-reason')).toHaveTextContent('Truck broke down')
    expect(screen.getByText('Stopped')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Stop route' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Complete trip' })).not.toBeInTheDocument()
  })

  it('guardian gets no end-route buttons', async () => {
    seedSession('guardian')
    mockBackend()
    renderAt('/routes/r-1/live')
    await screen.findByRole('heading', { name: 'Guadalajara → Querétaro' })
    expect(screen.queryByRole('button', { name: 'Stop route' })).not.toBeInTheDocument()
  })

  it('an unknown route reads "Route not found"', async () => {
    seedSession('guardian')
    mockBackend({ route: null })
    renderAt('/routes/r-1/live')
    expect(await screen.findByRole('heading', { name: 'Route not found' })).toBeInTheDocument()
  })
})
