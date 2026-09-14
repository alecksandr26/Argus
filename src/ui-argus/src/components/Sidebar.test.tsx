import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import Sidebar from './Sidebar'
import { AuthProvider } from '../context/AuthContext'
import type { LoginUser } from '../types'

const SESSION_USER: LoginUser = {
  id_user: 'usr-01',
  email: 'ana.torres@argus-flotas.mx',
  role: 'guardian',
  first_name: 'Ana',
  last_name: 'Torres',
}

function seedSession() {
  localStorage.setItem(
    'argus.session',
    JSON.stringify({ token: 'a-test-token', user: SESSION_USER }),
  )
}

function renderSidebar(initialPath = '/') {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={[initialPath]}>
        <Sidebar />
        <Routes>
          <Route path="/login" element={<div>Login screen</div>} />
          {/* Catch-all so navigating to any other in-app path (the tests below visit "/"
              and "/fleet") doesn't log React Router's "No routes matched" warning — Sidebar
              itself is what's under test, not the screens its links point to. */}
          <Route path="*" element={null} />
        </Routes>
      </MemoryRouter>
    </AuthProvider>,
  )
}

beforeEach(() => {
  seedSession()
})

describe('Sidebar', () => {
  it("renders the logged-in user's name and initials", () => {
    renderSidebar()
    expect(
      screen.getByText(`${SESSION_USER.first_name} ${SESSION_USER.last_name}`),
    ).toBeInTheDocument()
    const initials = (SESSION_USER.first_name[0] + SESSION_USER.last_name[0]).toUpperCase()
    expect(screen.getByText(initials)).toBeInTheDocument()
  })

  it('renders every real (non-"soon") nav link', () => {
    renderSidebar()
    expect(screen.getByRole('link', { name: /Live operations/ })).toHaveAttribute(
      'href',
      '/',
    )
    expect(screen.getByRole('link', { name: /Fleet/ })).toHaveAttribute(
      'href',
      '/fleet',
    )
    expect(screen.getByRole('link', { name: /Drivers/ })).toHaveAttribute(
      'href',
      '/drivers',
    )
    expect(
      screen.getByRole('link', { name: /Routes & trips/ }),
    ).toHaveAttribute('href', '/routes')
  })

  it('marks "soon" items with a soon badge', () => {
    renderSidebar()
    expect(screen.getByRole('link', { name: /Trip history/ })).toHaveTextContent(
      'soon',
    )
    expect(screen.getByRole('link', { name: /Access/ })).toHaveTextContent('soon')
  })

  it('highlights the active route', () => {
    renderSidebar('/fleet')
    expect(screen.getByRole('link', { name: /Fleet/ })).toHaveAttribute(
      'aria-current',
      'page',
    )
    expect(
      screen.getByRole('link', { name: /Live operations/ }),
    ).not.toHaveAttribute('aria-current')
  })

  it('clears the session and navigates to /login when the sign-out control is clicked', async () => {
    const user = userEvent.setup()
    renderSidebar()
    await user.click(screen.getByText('Sign out'))
    expect(screen.getByText('Login screen')).toBeInTheDocument()
    expect(localStorage.getItem('argus.session')).toBeNull()
  })
})
