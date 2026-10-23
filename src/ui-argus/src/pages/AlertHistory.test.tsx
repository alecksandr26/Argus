import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import AlertHistory from './AlertHistory'
import { AuthProvider } from '../context/AuthContext'
import { ALERT_HISTORY_POLL_MS } from '../constants'
import { at, makeAlert, makeIncident, makeRoute } from '../test/fixtures'
import type { Incident } from '../types'

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

const closed: Incident[] = [
  makeIncident({
    id_incident: 'i-1',
    severity: 'critical',
    tier: 'old',
    closed_at: at(600),
    closed_by: 'u1',
    close_note: 'Driver stopped to rest',
    alert_count: 2,
    alerts: [
      makeAlert({ id_alert: 'a-1', alert_type: 'eyes_closed' }),
      makeAlert({
        id_alert: 'a-2',
        alert_type: 'no_face',
        severity_level: 'critical',
        delivery: 'dlq',
        location_is_last_known: true,
        location_age_s: 90,
      }),
    ],
  }),
  makeIncident({
    id_incident: 'i-2',
    id_route: 'r-2',
    severity: 'medium',
    closed_at: at(900),
    truck_plate_number: 'ARG-002',
    alerts: [makeAlert({ id_alert: 'a-3', id_route: 'r-2', coordinates: null, position_unknown: true })],
  }),
]

function mockBackend() {
  const fetchMock = vi.fn(async (url: string) => {
    const u = new URL(url)
    if (u.pathname === '/api/incidents') return ok(closed)
    if (u.pathname === '/api/routes') return ok([makeRoute()])
    throw new Error(`unexpected GET ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderPage() {
  return render(
    <AuthProvider>
      <MemoryRouter>
        <AlertHistory />
      </MemoryRouter>
    </AuthProvider>,
  )
}

beforeEach(() => {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('AlertHistory', () => {
  it('lists the closed incidents (newest close first) with their alerts and positions', async () => {
    seedSession()
    const fetchMock = mockBackend()
    renderPage()

    const cards = await screen.findAllByTestId('incident-card')
    const call = fetchMock.mock.calls.map(([u]) => new URL(u)).find((u) => u.pathname === '/api/incidents')!
    expect(call.searchParams.get('status')).toBe('closed')

    expect(cards).toHaveLength(2)
    // i-2 was closed last.
    expect(within(cards[0]).getByTestId('alert-position')).toHaveTextContent('Position unknown')
    expect(within(cards[1]).getByText('“Driver stopped to rest”')).toBeInTheDocument()
    expect(within(cards[1]).getByText('Driver not visible')).toBeInTheDocument()
    expect(within(cards[1]).getByText('20.6597, -103.3496 · Last known (1 min old)')).toBeInTheDocument()
    expect(within(cards[1]).getByText('Recovered after lost connection')).toBeInTheDocument()
    expect(within(cards[1]).queryByRole('button', { name: 'Close incident' })).not.toBeInTheDocument()
    expect(screen.getByText('2 closed incidents · 3 alerts')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Guadalajara → Querétaro')).toBeInTheDocument())
  })

  it('filters by severity and by search text', async () => {
    seedSession()
    mockBackend()
    const user = userEvent.setup()
    renderPage()
    await screen.findAllByTestId('incident-card')

    await user.click(screen.getByRole('button', { name: 'Critical' }))
    expect(screen.getAllByTestId('incident-card')).toHaveLength(1)
    await user.click(screen.getByRole('button', { name: 'All' }))
    await user.type(screen.getByRole('textbox'), 'ARG-002')
    expect(screen.getAllByTestId('incident-card')).toHaveLength(1)
    expect(screen.getByText('Position unknown')).toBeInTheDocument()
  })

  it('re-polls every ALERT_HISTORY_POLL_MS', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    seedSession()
    const fetchMock = mockBackend()
    renderPage()
    await screen.findAllByTestId('incident-card')
    const count = () =>
      fetchMock.mock.calls.filter(([u]) => new URL(u).pathname === '/api/incidents').length
    expect(count()).toBe(1)
    await act(async () => {
      vi.advanceTimersByTime(ALERT_HISTORY_POLL_MS)
    })
    await waitFor(() => expect(count()).toBe(2))
  })
})
