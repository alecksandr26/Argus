import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import StatusPill from './StatusPill'
import type { Badge } from '../utils/status'

describe('StatusPill', () => {
  it('renders the badge label as text', () => {
    const badge: Badge = { label: 'Active', tone: 'good' }
    render(<StatusPill badge={badge} />)
    expect(screen.getByText('Active')).toBeInTheDocument()
  })

  it.each([
    ['good', 'pill--good'],
    ['warn', 'pill--warn'],
    ['bad', 'pill--bad'],
  ] as const)('maps tone %s to class %s', (tone, expectedClass) => {
    const badge: Badge = { label: 'x', tone }
    render(<StatusPill badge={badge} />)
    expect(screen.getByText('x')).toHaveClass('pill', expectedClass)
  })

  it('renders a neutral tone with no tone-specific class', () => {
    const badge: Badge = { label: 'Inactive', tone: 'neutral' }
    render(<StatusPill badge={badge} />)
    const el = screen.getByText('Inactive')
    expect(el).toHaveClass('pill')
    expect(el.className.trim()).toBe('pill')
  })
})
