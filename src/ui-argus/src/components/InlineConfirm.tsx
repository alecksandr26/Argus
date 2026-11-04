/**
 * A two-step confirm drawn in place ("Rotate key? ... — Confirm / Cancel") instead of
 * `window.confirm`. A native dialog can be suppressed by the browser ("prevent this page from
 * creating additional dialogs"), and then returns false without showing anything, so the action
 * silently never happened.
 */
export default function InlineConfirm({
  message,
  confirmLabel = 'Confirm',
  danger = false,
  busy = false,
  onConfirm,
  onCancel,
}: {
  message: string
  confirmLabel?: string
  danger?: boolean
  busy?: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <div
      role="group"
      aria-label={message}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        padding: 10,
        borderRadius: 8,
        border: '1px solid var(--border-soft)',
      }}
    >
      <span style={{ fontSize: 12 }}>{message}</span>
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          className={danger ? 'btn btn--danger' : 'btn btn--accent'}
          onClick={onConfirm}
          disabled={busy}
        >
          {busy ? 'Working…' : confirmLabel}
        </button>
        <button className="btn" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </div>
  )
}
