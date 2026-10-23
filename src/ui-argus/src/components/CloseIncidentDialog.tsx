import { useState } from 'react'
import Dialog from './Dialog'
import { closeIncident } from '../api/incidents'
import { ApiError } from '../api/client'
import { CLOSE_NOTE_MAX_LEN, closeNoteError } from '../utils/incidents'
import { severity } from '../utils/status'
import type { Incident } from '../types'

/**
 * "Close incident" (C3.6 `POST /api/incidents/{id}/close`). Only rendered for guardian and
 * root_admin (the caller checks `canCloseIncidents`). A critical incident needs a note: the
 * dialog says so before sending, the same rule the backend enforces with a 422. Closing closes
 * every alert of the incident; the driver's "OK" never does.
 */
export default function CloseIncidentDialog({
  incident,
  token,
  onCancel,
  onClosed,
}: {
  incident: Incident
  token: string
  onCancel: () => void
  onClosed: (incident: Incident) => void
}) {
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const critical = incident.severity === 'critical'

  async function submit() {
    const invalid = closeNoteError(incident.severity, note)
    if (invalid) {
      setError(invalid)
      return
    }
    setBusy(true)
    setError(null)
    try {
      onClosed(await closeIncident(incident.id_incident, note.trim(), token))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to close the incident')
      setBusy(false)
    }
  }

  const alerts = `${incident.alert_count} alert${incident.alert_count === 1 ? '' : 's'}`

  return (
    <Dialog title="Close incident" onClose={onCancel} busy={busy}>
      <p className="dialog__text">
        <strong>{severity[incident.severity].label}</strong> incident ·{' '}
        <span className="mono">{incident.truck_plate_number ?? '—'}</span> ·{' '}
        {incident.driver_full_name ?? '—'} · {alerts}
      </p>
      <p className="dialog__text">
        Closing it closes all {alerts}. The truck's map colour goes back to normal once none of
        its incidents is open.
      </p>
      <label className="field">
        <span className="field__label">
          {critical ? 'Note (required for a critical incident)' : 'Note (optional)'}
        </span>
        <textarea
          className="input"
          rows={4}
          maxLength={CLOSE_NOTE_MAX_LEN}
          placeholder="What happened, who you contacted, what was done…"
          value={note}
          onChange={(e) => {
            setNote(e.target.value)
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
        <button type="button" className="btn btn--accent" onClick={submit} disabled={busy}>
          {busy ? 'Closing…' : 'Close incident'}
        </button>
      </div>
    </Dialog>
  )
}
