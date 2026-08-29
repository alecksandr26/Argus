import Icon from './Icon'

/** Icon + text input used by the table screens to filter rows client-side. */
export default function SearchBox({
  value,
  onChange,
  placeholder,
}: {
  value: string
  onChange: (v: string) => void
  placeholder: string
}) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        background: 'var(--surface)',
        border: '1px solid var(--border-soft)',
        borderRadius: 8,
        padding: '8px 12px',
      }}
    >
      <Icon name="search" size={15} style={{ color: 'var(--text-faint)' }} />
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        style={{
          background: 'transparent',
          border: 'none',
          outline: 'none',
          color: 'var(--text)',
          font: 'inherit',
          fontSize: 12.5,
          width: 190,
        }}
      />
    </div>
  )
}
