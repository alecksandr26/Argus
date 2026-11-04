import { API_BASE_URL } from '../api/client'
import { rotateDeviceKey, type AlertPhoneSource, type DeviceKey } from '../api/trucks'
import type { Route, Truck } from '../types'

/**
 * The `argus-device.env` file a freshly-issued device key is handed out as. It's dropped next to
 * `src/cv-argus/docker-compose.yml` on the truck's Raspberry Pi; cv-argus reads these variables
 * at container start and hands them to the ESP32 over Bluetooth (`CONFIG`), which is the device
 * that actually calls the backend with them. The first three are the credentials; the plate and
 * the SMS alert phone (grip contracts C1) are what the ESP32's critical-alert SMS names and
 * texts. Since the route-guardian change the phone is the one the backend's rotate-key answer
 * resolved (contracts G5: the route guardian's, else the truck's fallback), with a comment line
 * naming where it came from (G7). See `src/cv-argus/README.md`.
 */

export const DEVICE_ENV_FILENAME = 'argus-device.env'

export interface DeviceEnvInput {
  truckId: string
  plate: string
  deviceApiKey: string
  backendUrl: string
  /** The SMS alert phone (E.164); null/empty writes the commented-out line instead. */
  alertPhone?: string | null
  /** Where `alertPhone` came from; adds the `# SMS alert phone: ...` comment line. */
  alertPhoneSource?: AlertPhoneSource | null
  guardianName?: string | null
  idRoute?: string | null
  issuedAt: Date
}

/** The phone a rotate-key answer carries, and where it came from. */
export interface IssuedPhone {
  phone: string | null
  source: AlertPhoneSource | null
  guardianName: string | null
  idRoute: string | null
}

/**
 * Reads the SMS phone out of a rotate-key answer (G5). A backend older than G5 sends no
 * `alert_phone` at all (`undefined`, not `null`); the file then carries the truck's own phone as
 * before, labelled as the truck's.
 */
export function issuedPhone(key: DeviceKey, truckPhone?: string | null): IssuedPhone {
  if (key.alert_phone === undefined) {
    const phone = truckPhone?.trim() || null
    return { phone, source: phone ? 'truck' : null, guardianName: null, idRoute: null }
  }
  const phone = key.alert_phone?.trim() || null
  return {
    phone,
    source: phone ? (key.alert_phone_source ?? null) : null,
    guardianName: key.guardian_name ?? null,
    idRoute: key.id_route ?? null,
  }
}

/** Trailing slashes are dropped so the ESP32 can append `/api/...` without doubling them. */
export function normalizeBackendUrl(url: string): string {
  return url.trim().replace(/\/+$/, '')
}

/** `ARGUS_ALERT_PHONE_LABEL`'s cap; cv-argus only logs it, so it never needs to be long. */
export const ALERT_PHONE_LABEL_MAX = 120

/** One line, no control characters: the env file is read line by line, unquoted. */
function oneLine(value: string): string {
  // eslint-disable-next-line no-control-regex
  return value.replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]+/g, ' ').replace(/\s+/g, ' ').trim()
}

function guardianDescription(input: DeviceEnvInput): string {
  return `guardian ${oneLine(input.guardianName ?? '') || 'unknown'} of route ${oneLine(input.idRoute ?? '') || 'unknown'}`
}

/**
 * The comment naming the phone's source (G7) plus `ARGUS_ALERT_PHONE_LABEL`, a display-only value
 * cv-argus logs next to the masked phone (never sent to the ESP32). Unquoted, since docker
 * compose's env_file takes the value literally, and capped at `ALERT_PHONE_LABEL_MAX`.
 */
function phoneSourceLines(input: DeviceEnvInput): { comment: string[]; label: string[] } {
  let comment: string
  let label: string
  if (input.alertPhoneSource === 'route_guardian') {
    comment = `# SMS alert phone: ${guardianDescription(input)}`
    label = guardianDescription(input)
  } else if (input.alertPhoneSource === 'truck') {
    comment = "# SMS alert phone: the truck's fallback phone"
    label = 'truck fallback phone'
  } else {
    return { comment: [], label: [] }
  }
  return {
    comment: [comment],
    label: [`ARGUS_ALERT_PHONE_LABEL=${label.slice(0, ALERT_PHONE_LABEL_MAX).trim()}`],
  }
}

export function buildDeviceEnv(input: DeviceEnvInput): string {
  const phone = input.alertPhone?.trim()
  const source = phoneSourceLines(input)
  return [
    `# Argus edge-device credentials — truck ${input.plate}`,
    `# Issued ${input.issuedAt.toISOString()}. Shown once: keep this file secret, never commit it.`,
    `# Place it next to src/cv-argus/docker-compose.yml on the truck's Raspberry Pi as`,
    `# ${DEVICE_ENV_FILENAME}, then restart the container (docker compose up -d).`,
    `ARGUS_TRUCK_ID=${input.truckId}`,
    `ARGUS_DEVICE_API_KEY=${input.deviceApiKey}`,
    `ARGUS_BACKEND_URL=${normalizeBackendUrl(input.backendUrl)}`,
    `ARGUS_TRUCK_PLATE=${input.plate}`,
    ...(phone
      ? [...source.comment, `ARGUS_ALERT_PHONE=${phone}`, ...source.label]
      : ['# ARGUS_ALERT_PHONE=  (no SMS alert phone set for this truck)']),
    '',
  ].join('\n')
}

/**
 * Saves `text` as a file. The anchor is attached to the document while it's clicked and the
 * object URL revoked only later: Safari/iOS and older Firefox ignore a click on a detached anchor
 * or a URL revoked in the same tick. `application/octet-stream` keeps browsers from appending
 * `.txt` to the `.env` name.
 */
export function downloadTextFile(text: string, filename: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: 'application/octet-stream' }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.style.display = 'none'
  document.body.appendChild(a)
  try {
    a.click()
  } finally {
    a.remove()
    setTimeout(() => {
      try {
        URL.revokeObjectURL(url)
      } catch {
        // Nothing to free (the page or its Blob support is gone).
      }
    }, 1000)
  }
}

/**
 * A key just issued for a truck, held by whoever shows it (Fleet, RouteDeviceFileButton) until the
 * user dismisses it. The backend keeps only a hash, so this is the only copy: it must outlive the
 * truck form's Save/Cancel, which is why it isn't state inside the credentials component.
 */
export interface IssuedCredentials {
  truckId: string
  plate: string
  key: DeviceKey
  /** The truck's own phone at issue time, used only when the backend predates G5. */
  truckPhone: string | null
  at: Date
  /** "Origin → Destination" when issued for one route (RouteDeviceFileButton). */
  routeLabel?: string
  /** That route's id, so the panel can tell whether the backend picked it. */
  requestedRoute?: string
}

/**
 * Rotates the truck's key (with `route`'s id when given, G5), and starts the env-file download
 * at once so the key is saved even if the page is closed right after. A failed download (a
 * blocked programmatic download, no Blob URLs) isn't an error: the panel's Download button
 * stays. Only the rotate call itself can reject.
 */
export async function issueDeviceCredentials(
  truck: Pick<Truck, 'id_truck' | 'plate_number' | 'alert_phone'>,
  token: string,
  route?: Pick<Route, 'id_route' | 'origin_name' | 'destination_name'>,
): Promise<IssuedCredentials> {
  const key = await rotateDeviceKey(truck.id_truck, token, route?.id_route)
  const issued: IssuedCredentials = {
    truckId: truck.id_truck,
    plate: truck.plate_number,
    key,
    truckPhone: truck.alert_phone ?? null,
    at: new Date(),
    ...(route
      ? {
          routeLabel: `${route.origin_name} → ${route.destination_name}`,
          requestedRoute: route.id_route,
        }
      : {}),
  }
  try {
    downloadTextFile(issuedEnvText(issued, normalizeBackendUrl(API_BASE_URL)), DEVICE_ENV_FILENAME)
  } catch {
    // The manual Download button is still there.
  }
  return issued
}

export function issuedEnvText(issued: IssuedCredentials, backendUrl: string): string {
  const p = issuedPhone(issued.key, issued.truckPhone)
  return buildDeviceEnv({
    truckId: issued.truckId,
    plate: issued.plate,
    deviceApiKey: issued.key.device_api_key,
    backendUrl,
    alertPhone: p.phone,
    alertPhoneSource: p.source,
    guardianName: p.guardianName,
    idRoute: p.idRoute,
    issuedAt: issued.at,
  })
}

/** True when a truck's device couldn't reach this URL (it only resolves on this machine). */
export function isLoopbackUrl(url: string): boolean {
  try {
    const host = new URL(url).hostname
    return host === 'localhost' || host === '127.0.0.1' || host === '[::1]'
  } catch {
    return false
  }
}
