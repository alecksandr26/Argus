import { useState } from 'react'
import { API_BASE_URL } from '../api/client'
import {
  DEVICE_ENV_FILENAME,
  downloadTextFile,
  isLoopbackUrl,
  issuedEnvText,
  issuedPhone,
  normalizeBackendUrl,
  type IssuedCredentials,
} from '../utils/deviceEnv'

/**
 * The one-time view of a freshly issued device key, packaged as `argus-device.env`. Shared by
 * Fleet (a box above the truck list, so it survives the truck form's Save/Cancel) and
 * RouteDeviceFileButton (a dialog). The download already started when the key was issued; this
 * keeps Download/Copy available until the user dismisses it with "I saved the file — close".
 */
export default function IssuedCredentialsPanel({
  issued,
  title,
  onDismiss,
}: {
  issued: IssuedCredentials
  title?: string
  onDismiss: () => void
}) {
  const [backendUrl, setBackendUrl] = useState(normalizeBackendUrl(API_BASE_URL))
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const envText = issuedEnvText(issued, backendUrl)
  const phone = issuedPhone(issued.key, issued.truckPhone)

  function download() {
    try {
      downloadTextFile(envText, DEVICE_ENV_FILENAME)
      setError(null)
    } catch {
      setError('Download failed — use Copy, or select the text and save it manually')
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(envText)
      setCopied(true)
      setError(null)
    } catch {
      setError('Copy failed — select the text and copy it manually')
    }
  }

  const routeName =
    issued.requestedRoute && issued.requestedRoute === phone.idRoute && issued.routeLabel
      ? `route ${issued.routeLabel}`
      : "this truck's current route"

  return (
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
      <span style={{ fontSize: 12.5, fontWeight: 600 }}>
        {title ?? `Device credentials ready for ${issued.plate}`}
      </span>
      <span style={{ fontSize: 11.5, color: 'var(--text-soft)' }}>
        Shown once: the backend keeps no copy. The download of{' '}
        <span className="mono">{DEVICE_ENV_FILENAME}</span> started when the key was issued; if it
        didn't arrive, use Download below. Put the file next to cv-argus's docker-compose.yml on
        this truck's Raspberry Pi, then restart the container. The ESP32 picks the key up from the
        Pi over Bluetooth.
      </span>
      <span data-testid="alert-phone-note" style={{ fontSize: 11.5, color: 'var(--text-soft)' }}>
        {phone.phone && phone.source === 'route_guardian' ? (
          <>
            Critical alerts are texted to guardian {phone.guardianName ?? 'unknown'} (
            <span className="mono">{phone.phone}</span>) of {routeName}.
          </>
        ) : phone.phone ? (
          <>
            Critical alerts are texted to the truck's fallback phone (
            <span className="mono">{phone.phone}</span>).
          </>
        ) : (
          "No SMS phone: assign a guardian to the truck's route or set the truck's alert phone."
        )}{' '}
        The file carries the phone as it is now. If the guardian or the truck's phone changes,
        save that change first, then rotate the key to get a new file (or edit the{' '}
        <span className="mono">ARGUS_ALERT_PHONE</span> line on the Pi and restart the container).
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
          This address only works on this computer. Use an address the truck can reach (LAN IP or
          the public HTTPS URL), then download again.
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
      {error && (
        <div role="alert" style={{ color: 'var(--danger, #e5484d)', fontSize: 12 }}>
          {error}
        </div>
      )}
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn btn--accent btn--block" onClick={download}>
          Download {DEVICE_ENV_FILENAME}
        </button>
        <button className="btn" onClick={copy}>
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <button className="btn btn--ghost" onClick={onDismiss}>
        I saved the file — close
      </button>
    </div>
  )
}
