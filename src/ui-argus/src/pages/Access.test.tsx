import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import Access from './Access'
import { AuthProvider } from '../context/AuthContext'
import type { LoginUser, User } from '../types'

const rootAdminUser: LoginUser = {
  id_user: 'root-1',
  email: 'root@argus.dev',
  role: 'root_admin',
  first_name: 'Root',
  last_name: 'Admin',
}

const adminUser: LoginUser = {
  id_user: 'admin-1',
  email: 'admin@argus.dev',
  role: 'admin',
  first_name: 'Fleet',
  last_name: 'Op',
}

const guardianRow: User = {
  id_user: 'g-1',
  email: 'guardian@argus.dev',
  role: 'guardian',
  first_name: 'Gina',
  last_name: 'Guard',
  phone_number: '+1-555-0001',
  is_active: true,
  last_login: null,
}

function seedSession(user: LoginUser) {
  localStorage.setItem('argus.session', JSON.stringify({ token: 'tok', user }))
}

function mockFetchList(users: User[]) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => users }),
  )
}

function renderAccess() {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={['/access']}>
        <Access />
      </MemoryRouter>
    </AuthProvider>,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Access', () => {
  it('root_admin sees "Add user" and every role in the create form', async () => {
    seedSession(rootAdminUser)
    mockFetchList([guardianRow])
    const user = userEvent.setup()
    renderAccess()

    expect(await screen.findByText(guardianRow.email)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Add user/ }))

    const roleSelect = screen.getByLabelText('Role') as HTMLSelectElement
    expect(roleSelect).not.toBeDisabled()
    const options = Array.from(roleSelect.options).map((o) => o.value)
    expect(options).toEqual(['root_admin', 'admin', 'guardian', 'truck_driver'])
  })

  it('admin sees "Add guardian" and a role field locked to guardian', async () => {
    seedSession(adminUser)
    mockFetchList([guardianRow])
    const user = userEvent.setup()
    renderAccess()

    expect(await screen.findByText(guardianRow.email)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Add guardian/ }))

    const roleSelect = screen.getByLabelText('Role') as HTMLSelectElement
    expect(roleSelect).toBeDisabled()
    expect(roleSelect.value).toBe('guardian')
    expect(Array.from(roleSelect.options).map((o) => o.value)).toEqual(['guardian'])
  })

  it('offers two distinct destructive actions on an existing user: deactivate vs. delete', async () => {
    seedSession(rootAdminUser)
    mockFetchList([guardianRow])
    const user = userEvent.setup()
    renderAccess()

    const row = await screen.findByText(guardianRow.email)
    await user.click(row)

    expect(screen.getByRole('button', { name: /Deactivate/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Delete permanently/ })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: /Save changes/ })).toBeEnabled())
  })
})

/** `GET /api/users` answers `users`; a POST/PUT echoes the body back as the saved user. */
function mockBackend(users: User[]) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body })
    if (init?.method === 'POST') {
      return ok({ ...guardianRow, id_user: 'new-1', ...JSON.parse(String(init.body)) })
    }
    if (init?.method === 'PUT') {
      const id = url.split('/').pop()
      const current = users.find((u) => u.id_user === id)!
      return ok({ ...current, ...JSON.parse(String(init.body)) })
    }
    return ok(users)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const writes = (fetchMock: ReturnType<typeof mockBackend>, method: string) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method === method)

async function fillNewUser(user: ReturnType<typeof userEvent.setup>, phone: string) {
  await user.type(screen.getByLabelText('First name'), 'Nina')
  await user.type(screen.getByLabelText('Last name'), 'Watch')
  await user.type(screen.getByLabelText('Email'), 'nina@argus.dev')
  await user.type(screen.getByLabelText('Password'), 'secret-pass')
  if (phone) await user.type(screen.getByLabelText('Phone'), phone)
}

describe('Access guardian phone (E.164)', () => {
  it('a guardian phone gets the E.164 hint, a tel input, and blocks Save while invalid', async () => {
    seedSession(adminUser)
    const fetchMock = mockBackend([guardianRow])
    const user = userEvent.setup()
    renderAccess()

    await user.click(await screen.findByRole('button', { name: /Add guardian/ }))
    const phone = screen.getByLabelText('Phone')
    expect(phone).toHaveAttribute('type', 'tel')
    expect(
      screen.getByText('E.164, e.g. +523312345678 — critical-alert SMS go to this number'),
    ).toBeInTheDocument()

    await fillNewUser(user, '33 1234 5678')
    expect(screen.getByRole('alert')).toHaveTextContent('Use the E.164 format, e.g. +523312345678')
    expect(phone).toHaveAttribute('aria-invalid', 'true')
    await user.click(screen.getByRole('button', { name: 'Create user' }))
    expect(writes(fetchMock, 'POST')).toHaveLength(0)
  })

  it('an empty guardian phone is refused on Save', async () => {
    seedSession(adminUser)
    const fetchMock = mockBackend([guardianRow])
    const user = userEvent.setup()
    renderAccess()

    await user.click(await screen.findByRole('button', { name: /Add guardian/ }))
    await fillNewUser(user, '')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Create user' }))
    expect(screen.getByRole('alert')).toHaveTextContent('A guardian needs a phone number')
    expect(writes(fetchMock, 'POST')).toHaveLength(0)
  })

  it('normalises spaces, dashes, dots and parentheses and sends the normalised phone', async () => {
    seedSession(adminUser)
    const fetchMock = mockBackend([guardianRow])
    const user = userEvent.setup()
    renderAccess()

    await user.click(await screen.findByRole('button', { name: /Add guardian/ }))
    await fillNewUser(user, ' +52 (33) 1234-56.78 ')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Create user' }))

    await waitFor(() => expect(writes(fetchMock, 'POST')).toHaveLength(1))
    const body = JSON.parse(String(writes(fetchMock, 'POST')[0][1]!.body))
    expect(body.role).toBe('guardian')
    expect(body.phone_number).toBe('+523312345678')
  })

  it('other roles keep a free-text phone, sent as typed', async () => {
    seedSession(rootAdminUser)
    const fetchMock = mockBackend([guardianRow])
    const user = userEvent.setup()
    renderAccess()

    await user.click(await screen.findByRole('button', { name: /Add user/ }))
    await user.selectOptions(screen.getByLabelText('Role'), 'truck_driver')
    expect(screen.queryByText(/critical-alert SMS go to this number/)).not.toBeInTheDocument()
    await fillNewUser(user, 'ext. 204')
    await user.click(screen.getByRole('button', { name: 'Create user' }))

    await waitFor(() => expect(writes(fetchMock, 'POST')).toHaveLength(1))
    const body = JSON.parse(String(writes(fetchMock, 'POST')[0][1]!.body))
    expect(body.role).toBe('truck_driver')
    expect(body.phone_number).toBe('ext. 204')
  })

  it('switching a user to guardian checks their phone', async () => {
    seedSession(rootAdminUser)
    const driverRow: User = {
      ...guardianRow,
      id_user: 'd-1',
      email: 'driver@argus.dev',
      role: 'truck_driver',
      phone_number: 'ext. 204',
    }
    const fetchMock = mockBackend([driverRow])
    const user = userEvent.setup()
    renderAccess()

    await user.click(await screen.findByText(driverRow.email))
    await user.selectOptions(screen.getByLabelText('Role'), 'guardian')
    expect(screen.getByRole('alert')).toHaveTextContent('Use the E.164 format')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(writes(fetchMock, 'PUT')).toHaveLength(0)

    await user.clear(screen.getByLabelText('Phone'))
    await user.type(screen.getByLabelText('Phone'), '+52 33 1234 5678')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(writes(fetchMock, 'PUT')).toHaveLength(1))
    const body = JSON.parse(String(writes(fetchMock, 'PUT')[0][1]!.body))
    expect(body).toMatchObject({ role: 'guardian', phone_number: '+523312345678' })
  })

  it("an existing guardian's untouched old phone doesn't block a name edit, and isn't sent", async () => {
    seedSession(adminUser)
    const legacy: User = { ...guardianRow, phone_number: 'call the office' }
    const fetchMock = mockBackend([legacy])
    const user = userEvent.setup()
    renderAccess()

    await user.click(await screen.findByText(legacy.email))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    await user.clear(screen.getByLabelText('Last name'))
    await user.type(screen.getByLabelText('Last name'), 'Guardia')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(writes(fetchMock, 'PUT')).toHaveLength(1))
    const body = JSON.parse(String(writes(fetchMock, 'PUT')[0][1]!.body))
    expect(body.last_name).toBe('Guardia')
    expect(body).not.toHaveProperty('phone_number')
    expect(body).not.toHaveProperty('role')
  })
})
