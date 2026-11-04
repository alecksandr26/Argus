import { useState } from 'react'
import Dialog from './Dialog'
import GuardianOptions from './GuardianOptions'
import { updateRoute } from '../api/routes'
import { ApiError } from '../api/client'
import type { Route, User } from '../types'

/**
 * "Assign guardian" / "Change guardian" for one route (route guardian contracts G1/G7):
 * `PUT /api/routes/{id}` with `{ id_guardian }`. The backend's 422 (`"id_guardian must be an
 * active guardian"`, `"guardian has no valid E.164 phone"`, ...) and 409 (`"route is finished"`)
 * show in the dialog as-is, which stays open so the user can pick another guardian.
 */
export default function AssignGuardianDialog({
  route,
  guardians,
  token,
  onCancel,
  onDone,
}: {
  route: Route
  /** Active guardian-role users (`selectableGuardians`). */
  guardians: User[]
  token: string
  onCancel: () => void
  onDone: (route: Route) => void
}) {
  const [selected, setSelected] = useState(
    guardians.some((g) => g.id_user === route.id_guardian) ? route.id_guardian! : '',
  )
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit() {
    if (!selected) {
      setError('Select a guardian')
      return
    }
    setBusy(true)
    setError(null)
    try {
      onDone(await updateRoute(route.id_route, { id_guardian: selected }, token))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to assign the guardian')
      setBusy(false)
    }
  }

  return (
    <Dialog title={route.guardian ? 'Change guardian' : 'Assign guardian'} onClose={onCancel} busy={busy}>
      <p className="dialog__text">
        The guardian of{' '}
        <strong>
          {route.origin_name} → {route.destination_name}
        </strong>{' '}
        watches it, and the truck texts their phone on every critical alert.
      </p>
      <label className="field">
        <span className="field__label">Guardian</span>
        <select
          className="input"
          value={selected}
          disabled={busy}
          onChange={(e) => {
            setSelected(e.target.value)
            setError(null)
          }}
        >
          <GuardianOptions guardians={guardians} />
        </select>
      </label>
      {guardians.length === 0 && (
        <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
          No active guardian yet. Add one in Access first.
        </span>
      )}
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
          {busy ? 'Saving…' : 'Save guardian'}
        </button>
      </div>
    </Dialog>
  )
}
