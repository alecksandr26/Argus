import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import Login from './Login'
import { AuthProvider } from '../context/AuthContext'

function renderLogin() {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={['/login']}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<div>Dashboard screen</div>} />
        </Routes>
      </MemoryRouter>
    </AuthProvider>,
  )
}

function mockFetchOnce(status: number, body: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    }),
  )
}

beforeEach(() => {
  mockFetchOnce(200, {})
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Login', () => {
  it('starts with an empty email field', () => {
    renderLogin()
    expect(screen.getByLabelText('Email address')).toHaveValue('')
  })

  it('is a controlled form — typing updates the field value', async () => {
    const user = userEvent.setup()
    renderLogin()
    const email = screen.getByLabelText('Email address')
    await user.type(email, 'someone@example.com')
    expect(email).toHaveValue('someone@example.com')
  })

  it('toggles "keep me signed in"', async () => {
    const user = userEvent.setup()
    renderLogin()
    const checkbox = screen.getByRole('checkbox', { name: /keep me signed in/i })
    expect(checkbox).toBeChecked()
    await user.click(checkbox)
    expect(checkbox).not.toBeChecked()
  })

  it('on successful login, navigates to the dashboard and stores the session', async () => {
    mockFetchOnce(200, {
      access_token: 'a-real-jwt',
      token_type: 'bearer',
      user: { id_user: '1', email: 'admin@example.com', role: 'root_admin', first_name: 'Root', last_name: 'Admin' },
    })
    const user = userEvent.setup()
    renderLogin()

    await user.type(screen.getByLabelText('Email address'), 'admin@example.com')
    await user.type(screen.getByLabelText('Password'), 'correct-password')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('Dashboard screen')).toBeInTheDocument()
    const stored = JSON.parse(localStorage.getItem('argus.session') ?? 'null')
    expect(stored.token).toBe('a-real-jwt')
    expect(stored.user.email).toBe('admin@example.com')
  })

  it('on failed login, shows an inline error and does not navigate', async () => {
    mockFetchOnce(401, { detail: 'Invalid email or password' })
    const user = userEvent.setup()
    renderLogin()

    await user.type(screen.getByLabelText('Email address'), 'admin@example.com')
    await user.type(screen.getByLabelText('Password'), 'wrong-password')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password')
    expect(screen.queryByText('Dashboard screen')).not.toBeInTheDocument()
    await waitFor(() => expect(localStorage.getItem('argus.session')).toBeNull())
  })
})
