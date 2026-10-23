import { useEffect, useId, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

/**
 * A small modal dialog (`role="dialog"`, `aria-modal`), titled by its heading. Escape or a click
 * on the backdrop calls `onClose` unless `busy` (a request is in flight). Used by the close
 * incident and complete/stop route dialogs.
 */
export default function Dialog({
  title,
  onClose,
  busy = false,
  children,
}: {
  title: string
  onClose: () => void
  busy?: boolean
  children: ReactNode
}) {
  const titleId = useId()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose, busy])

  // Portalled to <body> so it sits above the Leaflet map and outside table cells. React still
  // bubbles synthetic events up the component tree through a portal, so clicks are stopped here
  // (a dialog opened from a table row must not select that row).
  return createPortal(
    <div
      className="dialog-backdrop"
      onClick={(e) => e.stopPropagation()}
      onMouseDown={(e) => {
        e.stopPropagation()
        if (e.target === e.currentTarget && !busy) onClose()
      }}
    >
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <h2 id={titleId} className="dialog__title">
          {title}
        </h2>
        {children}
      </div>
    </div>,
    document.body,
  )
}
