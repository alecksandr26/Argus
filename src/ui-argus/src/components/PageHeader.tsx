import type { ReactNode } from 'react'

/** The title / subtitle / right-aligned actions row at the top of every screen. */
export default function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 16,
      }}
    >
      <div>
        <h1
          style={{
            fontFamily: 'var(--font-display)',
            fontWeight: 600,
            fontSize: 21,
            margin: 0,
          }}
        >
          {title}
        </h1>
        {subtitle && (
          <div
            style={{
              fontSize: 12.5,
              color: 'var(--text-faint)',
              marginTop: 2,
            }}
          >
            {subtitle}
          </div>
        )}
      </div>
      {actions && (
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          {actions}
        </div>
      )}
    </div>
  )
}
