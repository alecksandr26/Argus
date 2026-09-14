import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import RecordTable, { type Column } from './RecordTable'

interface Row {
  id: string
  name: string
}

const rows: Row[] = [
  { id: 'r1', name: 'Alpha' },
  { id: 'r2', name: 'Beta' },
]

const columns: Column<Row>[] = [
  { header: 'ID', cell: (r) => r.id },
  { header: 'Name', cell: (r) => r.name },
]

describe('RecordTable', () => {
  it('renders one header cell per column', () => {
    render(<RecordTable columns={columns} rows={rows} getId={(r) => r.id} />)
    expect(screen.getByText('ID')).toBeInTheDocument()
    expect(screen.getByText('Name')).toBeInTheDocument()
  })

  it('renders one row per record, with every column cell', () => {
    render(<RecordTable columns={columns} rows={rows} getId={(r) => r.id} />)
    expect(screen.getByText('Alpha')).toBeInTheDocument()
    expect(screen.getByText('Beta')).toBeInTheDocument()
    expect(screen.getAllByRole('row')).toHaveLength(rows.length + 1) // + header row
  })

  it('shows the empty-state label when there are no rows', () => {
    render(<RecordTable columns={columns} rows={[]} getId={(r) => r.id} />)
    expect(screen.getByText('No results.')).toBeInTheDocument()
  })

  it('shows a custom empty-state label when given one', () => {
    render(
      <RecordTable
        columns={columns}
        rows={[]}
        getId={(r) => r.id}
        emptyLabel="Nothing here yet"
      />,
    )
    expect(screen.getByText('Nothing here yet')).toBeInTheDocument()
  })

  it('calls onSelect with the clicked row', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(
      <RecordTable
        columns={columns}
        rows={rows}
        getId={(r) => r.id}
        onSelect={onSelect}
      />,
    )
    await user.click(screen.getByText('Beta'))
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith(rows[1])
  })

  it('marks the selected row with aria-selected', () => {
    render(
      <RecordTable
        columns={columns}
        rows={rows}
        getId={(r) => r.id}
        selectedId="r2"
      />,
    )
    const betaRow = screen.getByText('Beta').closest('tr')
    const alphaRow = screen.getByText('Alpha').closest('tr')
    expect(betaRow).toHaveAttribute('aria-selected', 'true')
    expect(alphaRow).toHaveAttribute('aria-selected', 'false')
  })
})
