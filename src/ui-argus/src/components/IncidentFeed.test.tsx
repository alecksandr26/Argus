import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import IncidentFeed from './IncidentFeed'
import { at, makeAlert, makeIncident } from '../test/fixtures'
import type { Incident, Role } from '../types'

afterEach(() => {
  vi.unstubAllGlobals()
})

function renderFeed(
  incidents: Incident[],
  { role = 'guardian' as Role, onShowOnMap = vi.fn(), onClosed = vi.fn(), routeHeaders = true } = {},
) {
  render(
    <MemoryRouter>
      <IncidentFeed
        incidents={incidents}
        role={role}
        token="tok"
        onShowOnMap={onShowOnMap}
        onClosed={onClosed}
        routeHeaders={routeHeaders}
      />
    </MemoryRouter>,
  )
  return { onShowOnMap, onClosed, user: userEvent.setup() }
}

const inc = (id: string, route: string, tier: Incident['tier'], last: number, over = {}) =>
  makeIncident({
    id_incident: id,
    id_route: route,
    tier,
    severity: tier === 'old' ? 'medium' : tier,
    last_alert_at: at(last),
    first_alert_at: at(last),
    truck_plate_number: `PLATE-${route}`,
    driver_full_name: `Driver ${route}`,
    alerts: [makeAlert({ id_alert: `${id}-a`, id_route: route, id_incident: id, timestamp: at(last) })],
    ...over,
  })

describe('IncidentFeed', () => {
  it('shows tier sections Critical → Medium → Old, routes grouped by their newest incident', () => {
    renderFeed([
      inc('m1', 'r-1', 'medium', 100),
      inc('o1', 'r-2', 'old', 10),
      inc('c1', 'r-1', 'critical', 50),
      inc('c2', 'r-2', 'critical', 200),
      inc('c3', 'r-1', 'critical', 30),
    ])
    const sections = screen.getAllByRole('region')
    expect(sections.map((s) => s.getAttribute('aria-label'))).toEqual([
      'Critical incidents',
      'Medium incidents',
      'Older than 1 h incidents',
    ])
    expect(within(sections[0]).getByRole('heading')).toHaveTextContent('Critical · 3')

    // In the critical tier: r-2 (newest, 200) first, then r-1 with its two incidents newest first.
    const groups = within(sections[0]).getAllByTestId('incident-route-group')
    expect(groups.map((g) => within(g).getAllByRole('link')[0].textContent)).toEqual([
      'PLATE-r-2 · Driver r-2',
      'PLATE-r-1 · Driver r-1',
    ])
    expect(within(groups[1]).getAllByTestId('incident-card')).toHaveLength(2)
    expect(within(groups[0]).getAllByRole('link')[0]).toHaveAttribute('href', '/routes/r-2/live')
  })

  it('an alert card shows its label, position, last-known label, replayed badge and driver OK', () => {
    renderFeed([
      makeIncident({
        severity: 'critical',
        tier: 'critical',
        has_replayed: true,
        driver_ok_at: '2026-10-23T12:05:00Z',
        alert_count: 3,
        alerts: [
          makeAlert({ id_alert: 'a-1', alert_type: 'eyes_closed' }),
          makeAlert({
            id_alert: 'a-2',
            alert_type: 'no_face',
            severity_level: 'critical',
            delivery: 'dlq',
            location_is_last_known: true,
            location_age_s: 180,
          }),
          makeAlert({
            id_alert: 'a-3',
            alert_type: 'something_new',
            coordinates: null,
            position_unknown: true,
          }),
        ],
      }),
    ])
    const cards = screen.getAllByTestId('alert-card')
    // Newest first.
    expect(within(cards[0]).getByRole('link')).toHaveTextContent('something_new')
    expect(within(cards[0]).getByTestId('alert-position')).toHaveTextContent('Position unknown')
    expect(within(cards[0]).queryByRole('button', { name: 'Show on map' })).not.toBeInTheDocument()

    expect(within(cards[1]).getByRole('link')).toHaveTextContent('Driver not visible')
    expect(within(cards[1]).getByTestId('alert-position')).toHaveTextContent(
      '20.6597, -103.3496 · Last known (3 min old)',
    )
    expect(within(cards[1]).getByText('Recovered after lost connection')).toBeInTheDocument()

    expect(within(cards[2]).getByRole('link')).toHaveTextContent('Eyes closed')
    expect(within(cards[2]).getByRole('link')).toHaveAttribute('href', '/alerts/a-1')
    expect(within(cards[2]).getByTestId('alert-position')).toHaveTextContent('20.6597, -103.3496')

    const card = screen.getByTestId('incident-card')
    expect(within(card).getByText('3 alerts')).toBeInTheDocument()
    expect(within(card).getByText(/^Driver OK since/)).toBeInTheDocument()
  })

  it('"Driver OK since" shows once per incident card, on its header, not again on its alerts', () => {
    renderFeed([
      makeIncident({
        driver_ok_at: '2026-10-23T12:05:00Z',
        alert_count: 2,
        alerts: [
          makeAlert({ id_alert: 'a-1', driver_ok_at: '2026-10-23T12:05:00Z' }),
          makeAlert({ id_alert: 'a-2', driver_ok_at: '2026-10-23T12:05:00Z' }),
        ],
      }),
    ])
    const card = screen.getByTestId('incident-card')
    expect(within(card).getAllByText(/^Driver OK since/)).toHaveLength(1)
    for (const alertCard of within(card).getAllByTestId('alert-card')) {
      expect(within(alertCard).queryByText(/^Driver OK since/)).not.toBeInTheDocument()
    }
  })

  it('"Show on map" hands the alert to the caller', async () => {
    const { onShowOnMap, user } = renderFeed([makeIncident()])
    await user.click(screen.getByRole('button', { name: 'Show on map' }))
    expect(onShowOnMap).toHaveBeenCalledWith(expect.objectContaining({ id_alert: 'a-1' }))
  })

  it.each<Role>(['admin', 'truck_driver'])('%s gets no Close button', (role) => {
    renderFeed([makeIncident()], { role })
    expect(screen.queryByRole('button', { name: 'Close incident' })).not.toBeInTheDocument()
  })

  it.each<Role>(['guardian', 'root_admin'])(
    '%s closes a critical incident through the dialog, with a required note',
    async (role) => {
      const incident = makeIncident({ id_incident: 'i-9', severity: 'critical', tier: 'critical' })
      const fetchMock = vi.fn(async (url: string) => ({
        ok: url.endsWith('/api/incidents/i-9/close'),
        status: 200,
        json: async () => ({ ...incident, closed_at: at(60) }),
      }))
      vi.stubGlobal('fetch', fetchMock)
      const { onClosed, user } = renderFeed([incident], { role })

      await user.click(screen.getByRole('button', { name: 'Close incident' }))
      const dialog = screen.getByRole('dialog', { name: 'Close incident' })
      await user.click(within(dialog).getByRole('button', { name: 'Close incident' }))
      expect(within(dialog).getByRole('alert')).toHaveTextContent('A note is required')
      expect(fetchMock).not.toHaveBeenCalled()

      await user.type(within(dialog).getByRole('textbox'), 'Driver rested 20 min')
      await user.click(within(dialog).getByRole('button', { name: 'Close incident' }))
      expect(String(fetchMock.mock.calls[0][0])).toContain('/api/incidents/i-9/close')
      expect(onClosed).toHaveBeenCalledWith(expect.objectContaining({ id_incident: 'i-9' }))
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    },
  )

  it('marks an ended route', () => {
    renderFeed([makeIncident({ route_ended: true })])
    expect(screen.getByText('Route ended')).toBeInTheDocument()
  })

  it('route headers can be hidden (the single-route view)', () => {
    renderFeed([makeIncident()], { routeHeaders: false })
    expect(screen.queryByText('ARG-001 · Ana Ruiz')).not.toBeInTheDocument()
  })

  it('empty feed', () => {
    renderFeed([])
    expect(screen.getByText('No open incidents.')).toBeInTheDocument()
  })
})
