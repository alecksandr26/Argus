/**
 * The truck's "SMS alert phone" (grip contracts C1): the number the ESP32 texts once per critical
 * alert. E.164 only, the same regex the backend, the Pi and the ESP32 check, so a number that
 * passes here is never silently dropped further down the chain.
 */

export const ALERT_PHONE_PATTERN = /^\+[1-9][0-9]{7,14}$/

export const ALERT_PHONE_EXAMPLE = '+523312345678'

/** Validation message for the alert phone, or `null` when it may be sent. Empty means "no SMS". */
export function alertPhoneError(phone: string): string | null {
  const trimmed = phone.trim()
  if (trimmed === '' || ALERT_PHONE_PATTERN.test(trimmed)) return null
  return `Use the international format, e.g. ${ALERT_PHONE_EXAMPLE}`
}

/**
 * The backend's `normalize_phone` (route guardian contracts G2): trims, then drops inner spaces,
 * `-`, `.`, `(` and `)`. `"+52 (33) 1234-5678"` → `"+523312345678"`. Send the normalised value,
 * so what the backend stores is exactly what the UI checked.
 */
export function normalizePhone(value: string): string {
  return value.trim().replace(/[\s\-.()]/g, '')
}

/** Whether `phone`, once normalised, is E.164 (a usable guardian / SMS alert phone). */
export function isE164Phone(phone: string): boolean {
  return ALERT_PHONE_PATTERN.test(normalizePhone(phone))
}

/** Hint under a guardian's phone field (G7): the number critical-alert SMS go to. */
export const GUARDIAN_PHONE_HINT = `E.164, e.g. ${ALERT_PHONE_EXAMPLE} — critical-alert SMS go to this number`

/** Validation message for a guardian's phone (G2: E.164 after normalisation), or `null` when it
 * may be sent. Unlike `alertPhoneError`, empty is an error: a guardian without a phone can't be
 * texted, and the backend rejects it. */
export function guardianPhoneError(phone: string): string | null {
  if (normalizePhone(phone) === '') return 'A guardian needs a phone number for critical-alert SMS'
  if (isE164Phone(phone)) return null
  return `Use the E.164 format, e.g. ${ALERT_PHONE_EXAMPLE}`
}
