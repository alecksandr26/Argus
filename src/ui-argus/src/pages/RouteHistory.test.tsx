import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import RouteHistory from './RouteHistory'
import { AuthProvider } from '../context/AuthContext'
import { at, makeAlert, makePoint, makeRoute } from '../test/fixtures'
import type { FleetMapMarker, MapPath, MapPin } from '../components/FleetMap'

// Leaflet doesn't run in jsdom; capture what the page hands the map instead.
const map = vi.hoisted(() => ({
  props: null as null | { markers: FleetMapMarker[]; paths?: MapPath[]; pins?: MapPin[] },
}))
vi.mock('../components/FleetMap', () => ({
  default: (props: typeof map.props) => {
    map.props = props
    return <div data-testid="fleet-map" />
  },
}))

function seedSession() {
  localStorage.setItem(
    'argus.session',
    JSON.stringify({
      token: 'tok',
      user: { id_user: 'u1', email: 'a@b.com', role: 'guardian', first_name: 'A', last_name: 'B' },
    }),
  )
}

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body })

function mockBackend() {
  const fetchMock = vi.fn(async (url: string) => {
    const u = new URL(url)
    if (u.pathname === '/api/routes') {
      return ok(
        u.searchParams.get('status') === 'completed'
          ? [makeRoute({ id_route: 'r-1', operative_status: 'completed', actual_arrival: at(3600) })]
          : [
              makeRoute({
                id_route: 'r-2',
                origin_name: 'León',
                destination_name: 'Celaya',
                operative_status: 'cancelled',
                stop_reason: 'Truck broke down',
              }),
            ],
      )
    }
    if (u.pathname === '/api/routes/r-1/track') {
      return ok({
        id_route: 'r-1',
        downsampled: true,
        points: [makePoint({ t: at(0) }), makePoint({ t: at(60), lat: 20.7 }), makePoint({ t: at(120), lat: 20.8 })],
      })
    }
    if (u.pathname === '/api/alerts') {
      return ok([
        makeAlert({
          id_alert: 'a-1',
          id_route: 'r-1',
          alert_type: 'head_down',
          delivery: 'dlq',
          location_is_last_known: true,
          location_age_s: 30,
        }),
      ])
    }
    if (u.pathname === '/api/drivers' || u.pathname === '/api/trucks') return ok([])
    throw new Error(`unexpected GET ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderPage() {
  return render(
    <AuthProvider>
      <MemoryRouter>
        <RouteHistory />
      </MemoryRouter>
    </AuthProvider>,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
  map.props = null
})

describe('RouteHistory', () => {
  it('lists completed and stopped routes', async () => {
    seedSession()
    mockBackend()
    renderPage()
    expect(await screen.findByText('Guadalajara → Querétaro')).toBeInTheDocument()
    expect(screen.getByText('León → Celaya')).toBeInTheDocument()
    expect(screen.getByText('Stopped')).toBeInTheDocument()
    expect(screen.getByText('2 ended trips · 1 alerts recorded')).toBeInTheDocument()
  })

  it('expanding a route fetches its GPS path once and draws it with the alert pins', async () => {
    seedSession()
    const fetchMock = mockBackend()
    const user = userEvent.setup()
    renderPage()

    const toggle = await screen.findByRole('button', { name: /Guadalajara → Querétaro/ })
    await user.click(toggle)
    const path = await screen.findByTestId('route-path')
    expect(within(path).getByText('GPS path · 3 points (downsampled)')).toBeInTheDocument()
    await waitFor(() => expect(map.props?.paths?.[0]?.positions).toHaveLength(3))
    expect(map.props!.pins!.map((p) => p.id)).toEqual(['a-1'])

    expect(screen.getByText('Head down')).toBeInTheDocument()
    expect(screen.getByText('20.6597, -103.3496 · Last known (30s old)')).toBeInTheDocument()
    expect(screen.getByText('Recovered after lost connection')).toBeInTheDocument()

    await user.click(toggle)
    await user.click(toggle)
    expect(
      fetchMock.mock.calls.filter(([u]) => String(u).includes('/api/routes/r-1/track')),
    ).toHaveLength(1)
  })

  it('a stopped route shows its reason when expanded', async () => {
    seedSession()
    mockBackend()
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /León → Celaya/ }))
    expect(screen.getByText('Stopped: Truck broke down')).toBeInTheDocument()
  })
})
