import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import CloseIncidentDialog from './CloseIncidentDialog'
import { makeIncident } from '../test/fixtures'

afterEach(() => {
  vi.unstubAllGlobals()
})

function ok(body: unknown) {
  return { ok: true, status: 200, json: async () => body }
}

function setup(severity: 'medium' | 'critical', fetchImpl?: ReturnType<typeof vi.fn>) {
  const incident = makeIncident({ id_incident: 'i-7', severity, tier: severity, alert_count: 2 })
  const fetchMock =
    fetchImpl ?? vi.fn(async () => ok({ ...incident, closed_at: '2026-10-23T12:30:00Z' }))
  vi.stubGlobal('fetch', fetchMock)
  const onClosed = vi.fn()
  const onCancel = vi.fn()
  render(
    <CloseIncidentDialog incident={incident} token="tok" onCancel={onCancel} onClosed={onClosed} />,
  )
  return { fetchMock, onClosed, onCancel, user: userEvent.setup() }
}

describe('CloseIncidentDialog', () => {
  it('a critical incident needs a note: empty or blank is refused without a request', async () => {
    const { fetchMock, onClosed, user } = setup('critical')
    expect(screen.getByRole('dialog', { name: 'Close incident' })).toBeInTheDocument()
    expect(screen.getByText('Note (required for a critical incident)')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Close incident' }))
    expect(screen.getByRole('alert')).toHaveTextContent(
      'A note is required to close a critical incident',
    )
    await user.type(screen.getByRole('textbox'), '   ')
    await user.click(screen.getByRole('button', { name: 'Close incident' }))
    expect(screen.getByRole('alert')).toHaveTextContent('A note is required')
    expect(fetchMock).not.toHaveBeenCalled()
    expect(onClosed).not.toHaveBeenCalled()
  })

  it('a critical incident with a note POSTs /close with the trimmed note', async () => {
    const { fetchMock, onClosed, user } = setup('critical')
    await user.type(screen.getByRole('textbox'), '  Called the driver, he stopped to rest. ')
    await user.click(screen.getByRole('button', { name: 'Close incident' }))

    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/api/incidents/i-7/close')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ note: 'Called the driver, he stopped to rest.' })
    expect(onClosed).toHaveBeenCalledWith(expect.objectContaining({ id_incident: 'i-7' }))
  })

  it('a medium incident may be closed without a note', async () => {
    const { fetchMock, onClosed, user } = setup('medium')
    expect(screen.getByText('Note (optional)')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Close incident' }))
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ note: '' })
    expect(onClosed).toHaveBeenCalled()
  })

  it('shows the backend error (e.g. 409 already closed) and stays open', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 409,
      json: async () => ({ detail: 'Incident already closed' }),
    }))
    const { onClosed, user } = setup('medium', fetchMock)
    await user.click(screen.getByRole('button', { name: 'Close incident' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Incident already closed')
    expect(onClosed).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('Cancel and Escape call onCancel', async () => {
    const { onCancel, user } = setup('medium')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await user.keyboard('{Escape}')
    expect(onCancel).toHaveBeenCalledTimes(2)
  })
})
