import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import RouteEndActions from './RouteEndActions'
import { makeRoute } from '../test/fixtures'
import type { Role, Route } from '../types'

afterEach(() => {
  vi.unstubAllGlobals()
})

function setup(route: Route, role: Role = 'admin') {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : {}
    const updated = String(url).endsWith('/stop')
      ? { ...route, operative_status: 'cancelled', stop_reason: body.reason }
      : { ...route, ...body }
    return { ok: true, status: 200, json: async () => updated }
  })
  vi.stubGlobal('fetch', fetchMock)
  const onChanged = vi.fn()
  render(<RouteEndActions route={route} role={role} token="tok" onChanged={onChanged} />)
  return { fetchMock, onChanged, user: userEvent.setup() }
}

describe('RouteEndActions', () => {
  it.each<Role>(['guardian', 'truck_driver'])('%s sees neither button', (role) => {
    setup(makeRoute({ operative_status: 'in_progress' }), role)
    expect(screen.queryByRole('button', { name: 'Complete trip' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Stop route' })).not.toBeInTheDocument()
  })

  it.each<Route['operative_status']>(['completed', 'cancelled'])(
    'nothing is offered for a %s route',
    (status) => {
      setup(makeRoute({ operative_status: status }), 'root_admin')
      expect(screen.queryByRole('button')).not.toBeInTheDocument()
    },
  )

  it('a scheduled route can be stopped but not completed', () => {
    setup(makeRoute({ operative_status: 'scheduled' }), 'root_admin')
    expect(screen.queryByRole('button', { name: 'Complete trip' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Stop route' })).toBeInTheDocument()
  })

  it('Stop route needs a reason, then POSTs /stop with it', async () => {
    const { fetchMock, onChanged, user } = setup(makeRoute({ id_route: 'r-5' }))
    await user.click(screen.getByRole('button', { name: 'Stop route' }))
    const dialog = screen.getByRole('dialog', { name: 'Stop route' })

    await user.click(within(dialog).getByRole('button', { name: 'Stop route' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent(
      'A reason is required to stop a route',
    )
    expect(fetchMock).not.toHaveBeenCalled()

    await user.type(within(dialog).getByRole('textbox'), ' Truck broke down ')
    await user.click(within(dialog).getByRole('button', { name: 'Stop route' }))
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/api/routes/r-5/stop')
    expect(init!.method).toBe('POST')
    expect(JSON.parse(String(init!.body))).toEqual({ reason: 'Truck broke down' })
    expect(onChanged).toHaveBeenCalledWith(
      expect.objectContaining({ operative_status: 'cancelled', stop_reason: 'Truck broke down' }),
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('Complete trip asks first, then PUTs completed with an arrival time', async () => {
    const { fetchMock, onChanged, user } = setup(makeRoute({ id_route: 'r-6' }), 'root_admin')
    await user.click(screen.getByRole('button', { name: 'Complete trip' }))
    const dialog = screen.getByRole('dialog', { name: 'Complete trip' })
    expect(dialog).toHaveTextContent('Open incidents stay open')
    expect(fetchMock).not.toHaveBeenCalled()

    await user.click(within(dialog).getByRole('button', { name: 'Complete trip' }))
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/api/routes/r-6')
    expect(init!.method).toBe('PUT')
    const body = JSON.parse(String(init!.body))
    expect(body.operative_status).toBe('completed')
    expect(Number.isNaN(Date.parse(body.actual_arrival))).toBe(false)
    // Never resolved_at (a 422 since C2.4), never anything that reopens a route.
    expect(Object.keys(body).sort()).toEqual(['actual_arrival', 'operative_status'])
    expect(onChanged).toHaveBeenCalled()
  })

  it('Cancel closes the dialog without a request', async () => {
    const { fetchMock, user } = setup(makeRoute())
    await user.click(screen.getByRole('button', { name: 'Stop route' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  describe('503 "Route is busy, try again" (C3.6 [sync 3])', () => {
    /** The first request answers `first`; the next ones succeed. */
    function setupBusy(route: Route, first: { status: number; body: unknown }) {
      let calls = 0
      const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
        calls += 1
        if (calls === 1) return { ok: false, status: first.status, json: async () => first.body }
        const body = init?.body ? JSON.parse(String(init.body)) : {}
        const updated = String(url).endsWith('/stop')
          ? { ...route, operative_status: 'cancelled', stop_reason: body.reason }
          : { ...route, ...body }
        return { ok: true, status: 200, json: async () => updated }
      })
      vi.stubGlobal('fetch', fetchMock)
      const onChanged = vi.fn()
      render(<RouteEndActions route={route} role="root_admin" token="tok" onChanged={onChanged} />)
      return { fetchMock, onChanged, user: userEvent.setup() }
    }

    it('Stop route: a retryable error inside the dialog, which stays open; pressing again retries', async () => {
      const { fetchMock, onChanged, user } = setupBusy(makeRoute({ id_route: 'r-7' }), {
        status: 503,
        body: { detail: 'Route is busy, try again' },
      })
      await user.click(screen.getByRole('button', { name: 'Stop route' }))
      const dialog = screen.getByRole('dialog', { name: 'Stop route' })
      await user.type(within(dialog).getByRole('textbox'), 'Truck broke down')
      await user.click(within(dialog).getByRole('button', { name: 'Stop route' }))

      expect(await within(dialog).findByRole('alert')).toHaveTextContent('Route is busy, try again')
      expect(screen.getByRole('dialog', { name: 'Stop route' })).toBeInTheDocument()
      expect(within(dialog).getByRole('textbox')).toHaveValue('Truck broke down')
      expect(onChanged).not.toHaveBeenCalled()

      await user.click(within(dialog).getByRole('button', { name: 'Stop route' }))
      expect(fetchMock).toHaveBeenCalledTimes(2)
      expect(JSON.parse(String(fetchMock.mock.calls[1][1]!.body))).toEqual({ reason: 'Truck broke down' })
      expect(onChanged).toHaveBeenCalledWith(expect.objectContaining({ operative_status: 'cancelled' }))
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('Complete trip: a 503 with any body reads as busy, the dialog stays open, then retries', async () => {
      const { fetchMock, onChanged, user } = setupBusy(makeRoute({ id_route: 'r-8' }), {
        status: 503,
        body: { message: 'Service Unavailable' },
      })
      await user.click(screen.getByRole('button', { name: 'Complete trip' }))
      const dialog = screen.getByRole('dialog', { name: 'Complete trip' })
      await user.click(within(dialog).getByRole('button', { name: 'Complete trip' }))

      expect(await within(dialog).findByRole('alert')).toHaveTextContent('Route is busy, try again')
      expect(screen.getByRole('dialog', { name: 'Complete trip' })).toBeInTheDocument()

      await user.click(within(dialog).getByRole('button', { name: 'Complete trip' }))
      expect(fetchMock).toHaveBeenCalledTimes(2)
      expect(onChanged).toHaveBeenCalledWith(expect.objectContaining({ operative_status: 'completed' }))
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('a 409 (route already ended) shows the server message, not the busy text', async () => {
      const { user } = setupBusy(makeRoute(), {
        status: 409,
        body: { detail: 'Route already ended' },
      })
      await user.click(screen.getByRole('button', { name: 'Stop route' }))
      const dialog = screen.getByRole('dialog', { name: 'Stop route' })
      await user.type(within(dialog).getByRole('textbox'), 'x')
      await user.click(within(dialog).getByRole('button', { name: 'Stop route' }))
      expect(await within(dialog).findByRole('alert')).toHaveTextContent('Route already ended')
    })
  })

  it('disabled greys out both buttons', () => {
    vi.stubGlobal('fetch', vi.fn())
    render(
      <RouteEndActions route={makeRoute()} role="admin" token="tok" onChanged={vi.fn()} disabled />,
    )
    expect(screen.getByRole('button', { name: 'Complete trip' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Stop route' })).toBeDisabled()
  })
})
