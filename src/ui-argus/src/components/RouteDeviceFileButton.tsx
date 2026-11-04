import { useState } from 'react'
import { ApiError } from '../api/client'
import { getTruck } from '../api/trucks'
import { issueDeviceCredentials, type IssuedCredentials } from '../utils/deviceEnv'
import Dialog from './Dialog'
import InlineConfirm from './InlineConfirm'
import IssuedCredentialsPanel from './IssuedCredentialsPanel'
import type { Route, Truck } from '../types'

/**
 * "Device file" for one route (route guardian contracts G7): rotates the route's truck key with
 * `id_route` = this route, so the backend puts this route's guardian phone in `argus-device.env`
 * (G5), starts the download at once and shows the same panel as Fleet, in a dialog that only
 * "I saved the file — close" dismisses (Escape and the backdrop don't: the key is shown once).
 *
 * Only `id_route`, `origin_name`, `destination_name` and `id_truck` are read from the route.
 * Rotating disconnects the installed device until the new file is on its Pi, so it confirms in
 * place first (not `window.confirm`, which a browser can suppress). For the caller to show to
 * root_admin/admin only: the backend rejects anyone else.
 */
export default function RouteDeviceFileButton({
  route,
  truck,
  token,
  onTruckChange,
}: {
  route: Route
  truck: Truck
  token: string
  onTruckChange?: (t: Truck) => void
}) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [issued, setIssued] = useState<IssuedCredentials | null>(null)

  async function issue() {
    setBusy(true)
    setError(null)
    try {
      setIssued(
        await issueDeviceCredentials(truck, token, {
          id_route: route.id_route,
          origin_name: route.origin_name,
          destination_name: route.destination_name,
        }),
      )
      setConfirming(false)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to issue the device file')
      setBusy(false)
      return
    }
    try {
      if (onTruckChange) onTruckChange(await getTruck(route.id_truck, token))
    } catch {
      // Only the key status is stale; the file is already issued.
    } finally {
      setBusy(false)
    }
  }

  const message = truck.has_device_key
    ? `Rotate ${truck.plate_number}'s key for route ${route.origin_name} → ${route.destination_name}? This disconnects the installed device until the new argus-device.env is on its Raspberry Pi.`
    : `Issue ${truck.plate_number}'s device credentials for route ${route.origin_name} → ${route.destination_name}?`

  return (
    // Stop clicks here from selecting a table row this button sits in.
    <div onClick={(e) => e.stopPropagation()} style={{ display: 'inline-flex', flexDirection: 'column', gap: 6 }}>
      {confirming ? (
        <InlineConfirm
          message={message}
          confirmLabel={truck.has_device_key ? 'Rotate key' : 'Issue file'}
          busy={busy}
          onConfirm={() => void issue()}
          onCancel={() => {
            setConfirming(false)
            setError(null)
          }}
        />
      ) : (
        <button className="btn btn--ghost" onClick={() => setConfirming(true)} disabled={busy}>
          Device file
        </button>
      )}
      {error && (
        <div role="alert" style={{ color: 'var(--danger, #e5484d)', fontSize: 12 }}>
          {error}
        </div>
      )}
      {issued && (
        <Dialog title={`Device file for ${issued.plate}`} onClose={() => {}}>
          <IssuedCredentialsPanel
            issued={issued}
            title={`Device credentials for route ${route.origin_name} → ${route.destination_name}`}
            onDismiss={() => setIssued(null)}
          />
        </Dialog>
      )}
    </div>
  )
}
