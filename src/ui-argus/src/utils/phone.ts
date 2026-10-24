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
