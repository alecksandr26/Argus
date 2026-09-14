import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import Sidebar from './Sidebar'
import { CURRENT_USER } from '../data/fixtures'

function renderSidebar(initialPath = '/') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Sidebar />
      <Routes>
        <Route path="/login" element={<div>Login screen</div>} />
        {/* Catch-all so navigating to any other in-app path (the tests below visit "/"
            and "/fleet") doesn't log React Router's "No routes matched" warning — Sidebar
            itself is what's under test, not the screens its links point to. */}
        <Route path="*" element={null} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Sidebar', () => {
  it("renders the current user's name and initials", () => {
    renderSidebar()
    expect(
      screen.getByText(`${CURRENT_USER.first_name} ${CURRENT_USER.last_name}`),
    ).toBeInTheDocument()
    const initials = (
      CURRENT_USER.first_name[0] + CURRENT_USER.last_name[0]
    ).toUpperCase()
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

  it('navigates to /login when the sign-out control is clicked', async () => {
    const user = userEvent.setup()
    renderSidebar()
    await user.click(screen.getByText('Sign out'))
    expect(screen.getByText('Login screen')).toBeInTheDocument()
  })
})
