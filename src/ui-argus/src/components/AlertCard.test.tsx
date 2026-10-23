import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import AlertCard from './AlertCard'
import { makeAlert } from '../test/fixtures'
import type { Alert } from '../types'

function renderCard({ alert, showDriverOk }: { alert?: Alert; showDriverOk?: boolean } = {}) {
  render(
    <MemoryRouter>
      <AlertCard
        alert={alert ?? makeAlert({ driver_ok_at: '2026-10-23T12:05:00Z' })}
        showDriverOk={showDriverOk}
      />
    </MemoryRouter>,
  )
}

describe('AlertCard', () => {
  it('a standalone card shows "Driver OK since" once the device reported the driver OK', () => {
    renderCard()
    expect(screen.getAllByText(/^Driver OK since/)).toHaveLength(1)
  })

  it('showDriverOk={false} (inside an incident card) leaves it to the incident header', () => {
    renderCard({ showDriverOk: false })
    expect(screen.queryByText(/^Driver OK since/)).not.toBeInTheDocument()
  })

  it('no driver OK yet: no pill', () => {
    renderCard({ alert: makeAlert({ driver_ok_at: null }) })
    expect(screen.queryByText(/^Driver OK since/)).not.toBeInTheDocument()
  })
})
