import { useState } from 'react'
import { ApiError, API_BASE_URL } from '../api/client'
import { getTruck, revokeDeviceKey, rotateDeviceKey } from '../api/trucks'
import { relativeTime } from '../utils/format'
import {
  DEVICE_ENV_FILENAME,
  buildDeviceEnv,
  isLoopbackUrl,
  normalizeBackendUrl,
} from '../utils/deviceEnv'
import type { Truck } from '../types'

/**
 * Fleet's "Device credentials" block: issues, rotates and revokes the truck's edge-device key
 * (`POST /api/trucks/:id/rotate-key`, `DELETE /api/trucks/:id/device-key`). A fresh key is shown
 * exactly once, packaged as `argus-device.env` for the truck's Raspberry Pi — the backend keeps
 * only a hash, so closing the panel without saving it means rotating again. The file also carries
 * the truck's plate and SMS alert phone as saved at that moment.
 */
export default function DeviceCredentials({
  truck,
  token,
  canWrite,
  onTruckChange,
}: {
  truck: Truck
  token: string
  canWrite: boolean
  onTruckChange: (t: Truck) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [issued, setIssued] = useState<{ key: string; at: Date } | null>(null)
  const [backendUrl, setBackendUrl] = useState(normalizeBackendUrl(API_BASE_URL))
  const [copied, setCopied] = useState(false)

  async function issue() {
    if (
      truck.has_device_key &&
      !window.confirm(
        `Rotate ${truck.plate_number}'s device key? The device currently installed stops ` +
          'working until the new argus-device.env is on its Raspberry Pi.',
      )
    )
      return
    setBusy(true)
    setError(null)
    try {
      const { device_api_key } = await rotateDeviceKey(truck.id_truck, token)
      setIssued({ key: device_api_key, at: new Date() })
      setCopied(false)
      onTruckChange(await getTruck(truck.id_truck, token))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to issue device credentials')
    } finally {
      setBusy(false)
    }
  }

  async function revoke() {
    if (
      !window.confirm(
        `Revoke ${truck.plate_number}'s device key? Its device is rejected immediately.`,
      )
    )
      return
    setBusy(true)
    setError(null)
    try {
      onTruckChange(await revokeDeviceKey(truck.id_truck, token))
      setIssued(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to revoke device credentials')
    } finally {
      setBusy(false)
    }
  }

  const envText = issued
    ? buildDeviceEnv({
        truckId: truck.id_truck,
        plate: truck.plate_number,
        deviceApiKey: issued.key,
        backendUrl,
        alertPhone: truck.alert_phone,
        issuedAt: issued.at,
      })
    : ''

  function download() {
    const url = URL.createObjectURL(new Blob([envText], { type: 'text/plain' }))
    const a = document.createElement('a')
    a.href = url
    a.download = DEVICE_ENV_FILENAME
    a.click()
    URL.revokeObjectURL(url)
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(envText)
      setCopied(true)
    } catch {
      setError('Copy failed — select the text and copy it manually')
    }
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

      {canWrite && (
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn--block" onClick={issue} disabled={busy}>
            {truck.has_device_key ? 'Rotate key' : 'Generate device credentials'}
          </button>
          {truck.has_device_key && (
            <button className="btn btn--danger" onClick={revoke} disabled={busy}>
              Revoke
            </button>
          )}
        </div>
      )}

      {issued && (
        <div
          role="region"
          aria-label="New device credentials"
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
            padding: 12,
            borderRadius: 8,
            border: '1px solid var(--border-soft)',
          }}
        >
          <span style={{ fontSize: 12, fontWeight: 600 }}>
            Shown once — save it now
          </span>
          <span style={{ fontSize: 11.5, color: 'var(--text-soft)' }}>
            Put <span className="mono">{DEVICE_ENV_FILENAME}</span> next to cv-argus's
            docker-compose.yml on this truck's Raspberry Pi, then restart the container. The
            ESP32 picks the key up from the Pi over Bluetooth.
          </span>
          <span data-testid="alert-phone-note" style={{ fontSize: 11.5, color: 'var(--text-soft)' }}>
            {truck.alert_phone ? (
              <>
                Critical alerts are texted to <span className="mono">{truck.alert_phone}</span>.
              </>
            ) : (
              'No SMS alert phone is set, so critical alerts send no SMS.'
            )}{' '}
            The file carries the phone as it is now: if you change it later, generate new
            credentials, or edit the <span className="mono">ARGUS_ALERT_PHONE</span> line on the
            Pi and restart the container.
          </span>
          <label className="field">
            <span className="field__label">Backend URL the truck uses</span>
            <input
              className="input mono"
              value={backendUrl}
              onChange={(e) => setBackendUrl(e.target.value)}
            />
          </label>
          {isLoopbackUrl(backendUrl) && (
            <span style={{ fontSize: 11.5, color: 'var(--warn, #f5a524)' }}>
              This address only works on this computer. Use an address the truck can reach
              (LAN IP or the public HTTPS URL).
            </span>
          )}
          <pre
            className="mono"
            data-testid="device-env"
            style={{
              margin: 0,
              fontSize: 10.5,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-all',
              userSelect: 'all',
            }}
          >
            {envText}
          </pre>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn--accent btn--block" onClick={download}>
              Download {DEVICE_ENV_FILENAME}
            </button>
            <button className="btn" onClick={copy}>
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
