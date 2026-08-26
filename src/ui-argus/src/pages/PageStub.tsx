/**
 * Placeholder used by every route until its real screen is built from the
 * approved mockups (see the "Argus — Mockups de UI" design canvas). Swap
 * each usage out one page at a time — delete PageStub once none remain.
 */
export default function PageStub({
  title,
  description,
}: {
  title: string
  description: string
}) {
  return (
    <div
      style={{
        minHeight: '100%',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '8px',
        padding: '48px',
        textAlign: 'center',
      }}
    >
      <span style={{ fontSize: '12px', color: 'var(--text-faint)' }}>
        ARGUS
      </span>
      <h1
        style={{
          fontFamily: 'var(--font-display)',
          fontSize: '22px',
          fontWeight: 600,
          margin: 0,
        }}
      >
        {title}
      </h1>
      <p
        style={{
          color: 'var(--text-soft)',
          fontSize: '13.5px',
          maxWidth: '440px',
          margin: 0,
        }}
      >
        {description}
      </p>
    </div>
  )
}
