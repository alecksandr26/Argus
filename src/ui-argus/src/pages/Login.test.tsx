import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import Login from './Login'
import { CURRENT_USER } from '../data/fixtures'

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<div>Dashboard screen</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Login', () => {
  it('pre-fills the email field from the current-user fixture', () => {
    renderLogin()
    expect(screen.getByLabelText('Email address')).toHaveValue(
      CURRENT_USER.email,
    )
  })

  it('is a controlled form — typing updates the field value', async () => {
    const user = userEvent.setup()
    renderLogin()
    const email = screen.getByLabelText('Email address')
    await user.clear(email)
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

  it('navigates to the dashboard on submit', async () => {
    const user = userEvent.setup()
    renderLogin()
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(screen.getByText('Dashboard screen')).toBeInTheDocument()
  })
})
