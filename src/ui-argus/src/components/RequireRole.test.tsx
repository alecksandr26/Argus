import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import RequireRole from './RequireRole'
import { AuthProvider } from '../context/AuthContext'
import type { LoginUser } from '../types'

function seedSession(role: LoginUser['role']) {
  localStorage.setItem(
    'argus.session',
    JSON.stringify({
      token: 'a-test-token',
      user: { id_user: 'u1', email: 'a@b.com', role, first_name: 'A', last_name: 'B' },
    }),
  )
}

function renderGuarded(roles: LoginUser['role'][]) {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={['/access']}>
        <Routes>
          <Route path="/" element={<div>Home screen</div>} />
          <Route element={<RequireRole roles={roles} />}>
            <Route path="/access" element={<div>Gated screen</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </AuthProvider>,
  )
}

describe('RequireRole', () => {
  it('renders the gated route when the session role is allowed', () => {
    seedSession('root_admin')
    renderGuarded(['root_admin', 'admin'])
    expect(screen.getByText('Gated screen')).toBeInTheDocument()
  })

  it('redirects to "/" when the session role is not allowed', () => {
    seedSession('guardian')
    renderGuarded(['root_admin', 'admin'])
    expect(screen.getByText('Home screen')).toBeInTheDocument()
    expect(screen.queryByText('Gated screen')).not.toBeInTheDocument()
  })

  it('redirects to "/" when there is no session at all', () => {
    renderGuarded(['root_admin', 'admin'])
    expect(screen.getByText('Home screen')).toBeInTheDocument()
  })
})
