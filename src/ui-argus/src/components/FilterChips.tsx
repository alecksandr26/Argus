/** A row of pill-shaped filter buttons — the same look `LiveOps.tsx`'s severity filter
 * originated, now shared with `RouteLiveView`'s "Route alerts" tab and `AlertHistory`. */
export interface ChipOption<T extends string> {
  key: T
  label: string
}

export default function FilterChips<T extends string>({
  options,
  value,
  onChange,
}: {
  options: ChipOption<T>[]
  value: T
  onChange: (v: T) => void
}) {
  return (
    <div style={{ display: 'flex', gap: 4 }}>
      {options.map((o) => (
        <button
          key={o.key}
          onClick={() => onChange(o.key)}
          style={{
            fontSize: 11,
            padding: '4px 10px',
            borderRadius: 20,
            border: 'none',
            cursor: 'pointer',
            font: 'inherit',
            background: value === o.key ? 'var(--surface-3)' : 'transparent',
            color: value === o.key ? 'var(--text)' : 'var(--text-faint)',
          }}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}
