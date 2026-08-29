import type { ReactNode } from 'react'

export interface Column<T> {
  header: string
  cell: (row: T) => ReactNode
}

/**
 * The shared table for the Fleet / Drivers / Routes screens: sticky-styled
 * header, hover + selected row states, click-to-select. Selection drives the
 * per-screen edit panel next to it.
 */
export default function RecordTable<T>({
  columns,
  rows,
  getId,
  selectedId,
  onSelect,
  emptyLabel = 'No results.',
}: {
  columns: Column<T>[]
  rows: T[]
  getId: (row: T) => string
  selectedId?: string | null
  onSelect?: (row: T) => void
  emptyLabel?: string
}) {
  return (
    <div
      className="panel"
      style={{ padding: '6px 6px 4px', flex: 1, overflow: 'auto' }}
    >
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.header}>{c.header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const id = getId(row)
            return (
              <tr
                key={id}
                aria-selected={selectedId === id}
                onClick={() => onSelect?.(row)}
                style={{ cursor: onSelect ? 'pointer' : 'default' }}
              >
                {columns.map((c) => (
                  <td key={c.header}>{c.cell(row)}</td>
                ))}
              </tr>
            )
          })}
          {rows.length === 0 && (
            <tr>
              <td
                colSpan={columns.length}
                style={{
                  color: 'var(--text-faint)',
                  textAlign: 'center',
                  padding: 28,
                  cursor: 'default',
                }}
              >
                {emptyLabel}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
