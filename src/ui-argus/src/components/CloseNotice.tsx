import { timeOfDay } from '../utils/format'
import { severity } from '../utils/status'
import type { Incident } from '../types'

/**
 * The confirmation after a successful "Close incident" (C3.7 `[sync 3]`), on Live operations and
 * the route view: "Critical incident closed · ABC-123 · Closed 14:32".
 *
 * - The live region (`role="status"`) is always mounted, so screen readers announce the text
 *   when it appears; it's empty until the first close.
 * - It is **not** auto-dismissed: the page replaces it on the next close and drops it on
 *   navigation (it lives in the page's state).
 * - `Closed HH:MM` uses `timeOfDay` (always a bare HH:MM, no "Yesterday"/date prefix) on the
 *   server's `closed_at`. it-argus matches `/Closed \d{1,2}:\d{2}/` inside this element.
 */
export default function CloseNotice({ incident }: { incident: Incident | null }) {
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="close-notice"
      style={
        incident
          ? {
              fontSize: 12,
              color: 'var(--good)',
              background: 'var(--good-soft)',
              border: '1px solid var(--good)',
              borderRadius: 8,
              padding: '6px 10px',
              flexShrink: 0,
            }
          : // Empty: out of the flex flow (no stray gap), still mounted as a live region.
            { position: 'absolute', width: 1, height: 1, overflow: 'hidden' }
      }
    >
      {incident && (
        <>
          {severity[incident.severity].label} incident closed
          {incident.truck_plate_number ? ` · ${incident.truck_plate_number}` : ''} · Closed{' '}
          {timeOfDay(incident.closed_at ?? new Date().toISOString())}
        </>
      )}
    </div>
  )
}
