import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SimulatorControl from './SimulatorControl'
import { SIMULATOR_POLL_MS } from '../constants'
import type { SimulatorStatus } from '../api/simulator'

const off: SimulatorStatus = {
  state: 'stopped',
  session_minutes: 10,
  started_at: null,
  auto_off_at: null,
  truck_count: null,
  quota_minutes: 120,
  quota_used_minutes: 0,
  quota_remaining_minutes: 120,
  sessions_remaining: 12,
}

function on(): SimulatorStatus {
  const start = Date.now()
  return {
    ...off,
    state: 'starting',
    started_at: new Date(start).toISOString(),
    auto_off_at: new Date(start + 10 * 60 * 1000).toISOString(),
    truck_count: 6,
    quota_used_minutes: 10,
    quota_remaining_minutes: 110,
    sessions_remaining: 11,
  }
}

function ok(body: unknown) {
  return { ok: true, status: 200, json: async () => body }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('SimulatorControl', () => {
  it('shows Off and the remaining quota', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(ok(off)))
    render(<SimulatorControl token="tok" />)
    expect(await screen.findByText('Off')).toBeInTheDocument()
    expect(screen.getByText(/12 of 12 sessions left/)).toBeInTheDocument()
    expect(screen.getByText(/Runs for 10 minutes/)).toBeInTheDocument()
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'false')
  })

  it('turning it on POSTs /api/simulator/start with only the truck count', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(ok(off)).mockResolvedValueOnce(ok(on()))
    vi.stubGlobal('fetch', fetchMock)
    render(<SimulatorControl token="tok" />)
    await screen.findByText('Off')

    await userEvent.click(screen.getByRole('switch'))

    expect(await screen.findByText('Starting…')).toBeInTheDocument()
    const [url, init] = fetchMock.mock.calls[1]
    expect(url).toMatch(/\/api\/simulator\/start$/)
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ truck_count: 6 })
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByLabelText('Time until auto-off')).toHaveTextContent(/Auto-off in (10:00|9:5\d)/)
  })

  it('turning it off POSTs /api/simulator/stop', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(ok(on())).mockResolvedValueOnce(ok(off))
    vi.stubGlobal('fetch', fetchMock)
    render(<SimulatorControl token="tok" />)
    await screen.findByText('Starting…')

    await userEvent.click(screen.getByRole('switch'))

    expect(await screen.findByText('Off')).toBeInTheDocument()
    expect(fetchMock.mock.calls[1][0]).toMatch(/\/api\/simulator\/stop$/)
  })

  it('polls the status every SIMULATOR_POLL_MS (10 s) while a session is open', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const fetchMock = vi.fn().mockResolvedValue(ok(on()))
      vi.stubGlobal('fetch', fetchMock)
      render(<SimulatorControl token="tok" />)
      await screen.findByText('Starting…')
      // The poll interval starts in a passive effect after this render; flush it before
      // advancing the fake clock, or the first advance can run before the interval exists.
      await act(async () => {})
      expect(SIMULATOR_POLL_MS).toBe(10000)
      const statusCalls = () =>
        fetchMock.mock.calls.filter(([url]) => /\/api\/simulator$/.test(url)).length
      expect(statusCalls()).toBe(1)

      await act(async () => {
        vi.advanceTimersByTime(SIMULATOR_POLL_MS - 1000)
      })
      expect(statusCalls()).toBe(1)
      await act(async () => {
        vi.advanceTimersByTime(1000)
      })
      await waitFor(() => expect(statusCalls()).toBe(2))
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not poll while it is off', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const fetchMock = vi.fn().mockResolvedValue(ok(off))
      vi.stubGlobal('fetch', fetchMock)
      render(<SimulatorControl token="tok" />)
      await screen.findByText('Off')
      await act(async () => {
        vi.advanceTimersByTime(SIMULATOR_POLL_MS * 3)
      })
      expect(fetchMock).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('is disabled when the monthly quota is used up', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(ok({ ...off, quota_used_minutes: 120, quota_remaining_minutes: 0, sessions_remaining: 0 })),
    )
    render(<SimulatorControl token="tok" />)
    await screen.findByText(/0 of 12 sessions left/)
    expect(screen.getByRole('switch')).toBeDisabled()
  })

  it('explains a 503 in plain words', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 503, json: async () => ({ detail: 'x' }) }),
    )
    render(<SimulatorControl token="tok" />)
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/not configured on this deployment/),
    )
  })
})
