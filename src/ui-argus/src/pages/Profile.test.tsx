import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import Profile from './Profile'
import { AuthProvider } from '../context/AuthContext'
import type { LoginUser, User } from '../types'

const sessionUser: LoginUser = {
  id_user: 'u-1',
  email: 'guardian@argus.dev',
  role: 'guardian',
  first_name: 'Gina',
  last_name: 'Guard',
}

const meRow: User = {
  id_user: 'u-1',
  email: 'guardian@argus.dev',
  role: 'guardian',
  first_name: 'Gina',
  last_name: 'Guard',
  phone_number: '+1-555-0001',
  is_active: true,
  last_login: null,
}

function seedSession() {
  localStorage.setItem('argus.session', JSON.stringify({ token: 'tok', user: sessionUser }))
}

function renderProfile() {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={['/profile']}>
        <Profile />
      </MemoryRouter>
    </AuthProvider>,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Profile', () => {
  it('loads and displays the caller\'s own profile fields, with no role/status control anywhere', async () => {
    seedSession()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => meRow }),
    )
    renderProfile()

    expect(await screen.findByDisplayValue('Gina')).toBeInTheDocument()
    expect(screen.getByDisplayValue('guardian@argus.dev')).toBeInTheDocument()
    expect(screen.queryByLabelText(/role/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/active/i)).not.toBeInTheDocument()
  })

  it('saves an edited phone number via PUT /api/auth/me', async () => {
    seedSession()
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => meRow })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ ...meRow, phone_number: '+1-555-9999' }),
      })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderProfile()

    const phone = await screen.findByLabelText('Phone')
    await user.clear(phone)
    await user.type(phone, '+1-555-9999')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(screen.getByText('Saved')).toBeInTheDocument())
    const [, putCall] = fetchMock.mock.calls
    expect(putCall[0]).toContain('/api/auth/me')
    expect(putCall[1].method).toBe('PUT')
    const body = JSON.parse(putCall[1].body)
    // A guardian's phone goes out normalised (route guardian contracts G2).
    expect(body.phone_number).toBe('+15559999')
    expect(body).not.toHaveProperty('role')
    expect(body).not.toHaveProperty('is_active')
  })

  it("refuses a guardian's non-E.164 phone, with the hint", async () => {
    seedSession()
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, status: 200, json: async () => meRow })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderProfile()

    const phone = await screen.findByLabelText('Phone')
    expect(phone).toHaveAttribute('type', 'tel')
    expect(screen.getByText(/critical-alert SMS go to this number/)).toBeInTheDocument()
    await user.clear(phone)
    await user.type(phone, '33 1234 5678')
    expect(screen.getAllByText('Use the E.164 format, e.g. +523312345678').length).toBeGreaterThan(0)
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(fetchMock).toHaveBeenCalledTimes(1) // only the GET
  })

  it("another role's phone stays free text, and an unchanged phone isn't sent", async () => {
    localStorage.setItem(
      'argus.session',
      JSON.stringify({ token: 'tok', user: { ...sessionUser, role: 'admin' } }),
    )
    const adminRow: User = { ...meRow, role: 'admin', phone_number: 'ext. 204' }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => adminRow })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => adminRow })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderProfile()

    const first = await screen.findByLabelText('First name')
    expect(screen.queryByText(/critical-alert SMS go to this number/)).not.toBeInTheDocument()
    await user.type(first, 'x')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).not.toHaveProperty('phone_number')
  })
})
