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
