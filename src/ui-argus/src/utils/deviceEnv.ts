/**
 * The `argus-device.env` file a freshly-issued device key is handed out as. It's dropped next to
 * `src/cv-argus/docker-compose.yml` on the truck's Raspberry Pi; cv-argus reads these three
 * variables at container start and hands them to the ESP32 over Bluetooth (`CONFIG`), which is
 * the device that actually calls the backend with them. See `src/cv-argus/README.md`.
 */

export const DEVICE_ENV_FILENAME = 'argus-device.env'

export interface DeviceEnvInput {
  truckId: string
  plate: string
  deviceApiKey: string
  backendUrl: string
  issuedAt: Date
}

/** Trailing slashes are dropped so the ESP32 can append `/api/...` without doubling them. */
export function normalizeBackendUrl(url: string): string {
  return url.trim().replace(/\/+$/, '')
}

export function buildDeviceEnv(input: DeviceEnvInput): string {
  return [
    `# Argus edge-device credentials — truck ${input.plate}`,
    `# Issued ${input.issuedAt.toISOString()}. Shown once: keep this file secret, never commit it.`,
    `# Place it next to src/cv-argus/docker-compose.yml on the truck's Raspberry Pi as`,
    `# ${DEVICE_ENV_FILENAME}, then restart the container (docker compose up -d).`,
    `ARGUS_TRUCK_ID=${input.truckId}`,
    `ARGUS_DEVICE_API_KEY=${input.deviceApiKey}`,
    `ARGUS_BACKEND_URL=${normalizeBackendUrl(input.backendUrl)}`,
    '',
  ].join('\n')
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
