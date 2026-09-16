import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import Fleet from './Fleet'
import { AuthProvider } from '../context/AuthContext'
import type { LoginUser, Truck } from '../types'

const truckRow: Truck = {
  id_truck: 't-1',
  plate_number: 'ARG-001',
  brand: 'Kenworth',
  model: 'T680',
  company_number: 'TR-001',
  raspberry_pi_mac: null,
  esp32_id: null,
  operative_status: 'active',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

function seedSession(role: LoginUser['role']) {
  localStorage.setItem(
    'argus.session',
    JSON.stringify({
      token: 'tok',
      user: { id_user: 'u1', email: 'a@b.com', role, first_name: 'A', last_name: 'B' },
    }),
  )
}

function mockFetchList() {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => [truckRow] }),
  )
}

function renderFleet() {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={['/fleet']}>
        <Fleet />
      </MemoryRouter>
    </AuthProvider>,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Fleet write-gating', () => {
  it('root_admin sees "Add truck" and can edit an existing row', async () => {
    seedSession('root_admin')
    mockFetchList()
    const user = userEvent.setup()
    renderFleet()

    expect(screen.getByRole('button', { name: /Add truck/ })).toBeInTheDocument()
    await user.click(await screen.findByText('ARG-001'))
    expect(screen.getByText('Edit truck')).toBeInTheDocument()
    expect(screen.getByLabelText('Plate')).not.toBeDisabled()
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeInTheDocument()
  })

  it('guardian has no "Add truck" button and sees rows read-only', async () => {
    seedSession('guardian')
    mockFetchList()
    const user = userEvent.setup()
    renderFleet()

    expect(screen.queryByRole('button', { name: /Add truck/ })).not.toBeInTheDocument()
    await user.click(await screen.findByText('ARG-001'))
    expect(screen.getByText('View truck')).toBeInTheDocument()
    expect(screen.getByLabelText('Plate')).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Save changes' })).not.toBeInTheDocument()
    // Two elements share the accessible name "Close" here (the icon-only dismiss button's
    // aria-label, and this panel's text button) — assert on the text node specifically.
    expect(screen.getByText('Close')).toBeInTheDocument()
  })
})
