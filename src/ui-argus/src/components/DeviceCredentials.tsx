import { useState } from 'react'
import { ApiError } from '../api/client'
import { getTruck, revokeDeviceKey } from '../api/trucks'
import { relativeTime } from '../utils/format'
import { issueDeviceCredentials, type IssuedCredentials } from '../utils/deviceEnv'
import InlineConfirm from './InlineConfirm'
import type { Truck } from '../types'

/**
 * Fleet's "Device credentials" block: issues, rotates and revokes the truck's edge-device key
 * (`POST /api/trucks/:id/rotate-key`, `DELETE /api/trucks/:id/device-key`).
 *
 * - A fresh key is handed to `onIssued` and its `argus-device.env` download starts at once. The
 *   key itself is NOT kept here: this block unmounts whenever the truck form closes (Save,
 *   Cancel, picking another truck), and the backend keeps only a hash, so Fleet holds it and
 *   shows `IssuedCredentialsPanel` until the user dismisses it. (Keeping it here lost the file
 *   on Save before; see ui-argus CLAUDE.md.)
 * - Rotate and Revoke confirm in place (`InlineConfirm`), not with `window.confirm`, which a
 *   browser may suppress (it then returns false and the rotate silently never happens).
 * - With unsaved changes in the truck form (`dirty`), issuing is disabled with a "save the truck
 *   first" hint: the file would otherwise carry the saved values, not the typed ones.
 */
export default function DeviceCredentials({
  truck,
  token,
  canWrite,
  dirty = false,
  hasIssued = false,
  onIssued,
  onRevoked,
  onTruckChange,
}: {
  truck: Truck
  token: string
  canWrite: boolean
  /** The truck form has unsaved changes. */
  dirty?: boolean
  /** A key issued for this truck is still on screen (Fleet's box above the list). */
  hasIssued?: boolean
  onIssued: (issued: IssuedCredentials) => void
  onRevoked?: (t: Truck) => void
  onTruckChange: (t: Truck) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<'rotate' | 'revoke' | null>(null)

  async function issue() {
    setBusy(true)
    setError(null)
    try {
      onIssued(await issueDeviceCredentials(truck, token))
      setConfirming(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to issue device credentials')
      setBusy(false)
      return
    }
    // The key is safe with Fleet already; a failed status refresh mustn't look like a failed issue.
    try {
      onTruckChange(await getTruck(truck.id_truck, token))
    } catch {
      // The status line just stays stale until the next load.
    } finally {
      setBusy(false)
    }
  }

  async function revoke() {
    setBusy(true)
    setError(null)
    try {
      const updated = await revokeDeviceKey(truck.id_truck, token)
      onTruckChange(updated)
      onRevoked?.(updated)
      setConfirming(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to revoke device credentials')
    } finally {
      setBusy(false)
    }
  }

  function startIssue() {
    if (truck.has_device_key) setConfirming('rotate')
    else void issue()
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <span style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--text-soft)' }}>
        Device credentials
      </span>
      <div data-testid="device-key-status" style={{ fontSize: 12, color: 'var(--text-soft)' }}>
        {truck.has_device_key ? (
          <>
            Key issued{' '}
            {truck.device_key_issued_at ? relativeTime(truck.device_key_issued_at) : ''} ·{' '}
            {truck.device_last_seen_at
              ? `last seen ${relativeTime(truck.device_last_seen_at)}`
              : 'device not seen yet'}
          </>
        ) : (
          <span style={{ color: 'var(--text-faint)' }}>No credentials issued</span>
        )}
      </div>

      {error && (
        <div role="alert" style={{ color: 'var(--danger, #e5484d)', fontSize: 12 }}>
          {error}
        </div>
      )}

      {canWrite && confirming === 'rotate' && (
        <InlineConfirm
          message={`Rotate ${truck.plate_number}'s key? This disconnects the installed device until the new argus-device.env is on its Raspberry Pi.`}
          confirmLabel="Rotate key"
          busy={busy}
          onConfirm={() => void issue()}
          onCancel={() => setConfirming(null)}
        />
      )}
      {canWrite && confirming === 'revoke' && (
        <InlineConfirm
          message={`Revoke ${truck.plate_number}'s key? Its device is rejected immediately.`}
          confirmLabel="Revoke key"
          danger
          busy={busy}
          onConfirm={() => void revoke()}
          onCancel={() => setConfirming(null)}
        />
      )}

      {canWrite && confirming === null && (
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn--block" onClick={startIssue} disabled={busy || dirty}>
            {truck.has_device_key ? 'Rotate key' : 'Generate device credentials'}
          </button>
          {truck.has_device_key && (
            <button
              className="btn btn--danger"
              onClick={() => setConfirming('revoke')}
              disabled={busy}
            >
              Revoke
            </button>
          )}
        </div>
      )}

      {canWrite && dirty && (
        <span data-testid="device-unsaved" style={{ fontSize: 11.5, color: 'var(--warn, #f5a524)' }}>
          The truck has unsaved changes. Save the truck first, then rotate the key, so the file
          carries what you typed.
        </span>
      )}

      {hasIssued && (
        <span data-testid="device-issued-hint" style={{ fontSize: 11.5, color: 'var(--text-soft)' }}>
          New credentials were issued: <span className="mono">argus-device.env</span> is above the
          truck list until you close it.
        </span>
      )}
    </div>
  )
}
