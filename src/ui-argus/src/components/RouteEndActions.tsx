import { useState } from 'react'
import Dialog from './Dialog'
import { stopRoute, updateRoute } from '../api/routes'
import { ApiError } from '../api/client'
import { STOP_REASON_MAX_LEN, canEndRoutes, canStopRoute, stopReasonError } from '../utils/incidents'
import { isActiveRoute } from '../utils/status'
import type { Role, Route } from '../types'

type Pending = 'complete' | 'stop' | null

/**
 * "Complete trip" and "Stop route" for one route, each behind a confirmation dialog, used by
 * Routes (TravelManagement), Live operations and the route view.
 *
 * - Shown only to root_admin and admin (`canEndRoutes`), and only while the route hasn't ended:
 *   nothing renders for a `completed`/`cancelled` route.
 * - "Complete trip" (in-progress routes) is the existing `PUT` to `completed`.
 * - "Stop route" (`POST /api/routes/{id}/stop`, C3.6) needs a reason; the route becomes
 *   `cancelled` ("Stopped"). It's also offered for a scheduled route.
 * - Either way the route's open incidents stay open, and the backend rejects everything the
 *   truck's device sends for this route from then on.
 */
export default function RouteEndActions({
  route,
  role,
  token,
  onChanged,
  compact = false,
}: {
  route: Route
  role: Role
  token: string
  onChanged: (route: Route) => void
  /** Small ghost buttons for table rows and lists. */
  compact?: boolean
}) {
  const [pending, setPending] = useState<Pending>(null)
  if (!canEndRoutes(role) || !canStopRoute(route)) return null

  const btnClass = compact ? 'btn btn--ghost' : 'btn'
  const btnStyle = compact ? { padding: '4px 10px', fontSize: 11.5 } : undefined
  const done = (r: Route) => {
    setPending(null)
    onChanged(r)
  }

  return (
    <>
      {isActiveRoute(route.operative_status) && (
        <button
          type="button"
          className={btnClass}
          style={btnStyle}
          onClick={(e) => {
            e.stopPropagation()
            setPending('complete')
          }}
        >
          Complete trip
        </button>
      )}
      <button
        type="button"
        className={compact ? btnClass : 'btn btn--danger'}
        style={btnStyle}
        onClick={(e) => {
          e.stopPropagation()
          setPending('stop')
        }}
      >
        Stop route
      </button>
      {pending === 'complete' && (
        <CompleteRouteDialog
          route={route}
          token={token}
          onCancel={() => setPending(null)}
          onDone={done}
        />
      )}
      {pending === 'stop' && (
        <StopRouteDialog route={route} token={token} onCancel={() => setPending(null)} onDone={done} />
      )}
    </>
  )
}

const routeName = (r: Route) => `${r.origin_name} → ${r.destination_name}`

export function CompleteRouteDialog({
  route,
  token,
  onCancel,
  onDone,
}: {
  route: Route
  token: string
  onCancel: () => void
  onDone: (route: Route) => void
}) {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      onDone(
        await updateRoute(
          route.id_route,
          { operative_status: 'completed', actual_arrival: new Date().toISOString() },
          token,
        ),
      )
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to complete the trip')
      setBusy(false)
    }
  }

  return (
    <Dialog title="Complete trip" onClose={onCancel} busy={busy}>
      <p className="dialog__text">
        Mark <strong>{routeName(route)}</strong> as completed? Open incidents stay open, and
        anything the truck sends for this route from now on is rejected.
      </p>
      {error && (
        <div role="alert" className="field__error">
          {error}
        </div>
      )}
      <div className="dialog__actions">
        <button type="button" className="btn" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button type="button" className="btn btn--accent" onClick={submit} disabled={busy}>
          {busy ? 'Completing…' : 'Complete trip'}
        </button>
      </div>
    </Dialog>
  )
}

export function StopRouteDialog({
  route,
  token,
  onCancel,
  onDone,
}: {
  route: Route
  token: string
  onCancel: () => void
  onDone: (route: Route) => void
}) {
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit() {
    const invalid = stopReasonError(reason)
    if (invalid) {
      setError(invalid)
      return
    }
    setBusy(true)
    setError(null)
    try {
      onDone(await stopRoute(route.id_route, reason.trim(), token))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to stop the route')
      setBusy(false)
    }
  }

  return (
    <Dialog title="Stop route" onClose={onCancel} busy={busy}>
      <p className="dialog__text">
        Stop <strong>{routeName(route)}</strong> before it's completed? Open incidents stay
        open, and anything the truck sends for this route from now on is rejected.
      </p>
      <label className="field">
        <span className="field__label">Reason (required)</span>
        <textarea
          className="input"
          rows={3}
          maxLength={STOP_REASON_MAX_LEN}
          placeholder="e.g. truck broke down, driver relieved, trip cancelled by the client"
          value={reason}
          onChange={(e) => {
            setReason(e.target.value)
            setError(null)
          }}
          disabled={busy}
        />
      </label>
      {error && (
        <div role="alert" className="field__error">
          {error}
        </div>
      )}
      <div className="dialog__actions">
        <button type="button" className="btn" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button type="button" className="btn btn--danger" onClick={submit} disabled={busy}>
          {busy ? 'Stopping…' : 'Stop route'}
        </button>
      </div>
    </Dialog>
  )
}
