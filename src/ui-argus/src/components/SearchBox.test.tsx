import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SearchBox from './SearchBox'

describe('SearchBox', () => {
  it('renders the given value and placeholder', () => {
    render(
      <SearchBox value="alpha" onChange={() => {}} placeholder="Search trucks…" />,
    )
    const input = screen.getByPlaceholderText('Search trucks…')
    expect(input).toHaveValue('alpha')
  })

  it('calls onChange with the new value as the user types', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<SearchBox value="" onChange={onChange} placeholder="Search…" />)
    await user.type(screen.getByPlaceholderText('Search…'), 'ab')
    // Controlled input with a mocked onChange never updates `value` between keystrokes,
    // so each keystroke fires against the same starting value — that's expected here,
    // the point is confirming every keystroke reaches the handler with the right character.
    expect(onChange).toHaveBeenNthCalledWith(1, 'a')
    expect(onChange).toHaveBeenNthCalledWith(2, 'b')
  })
})
