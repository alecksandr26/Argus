import { describe, it, expect } from 'vitest'
import { ALERT_PHONE_EXAMPLE, alertPhoneError } from './phone'

describe('alertPhoneError', () => {
  it.each(['', '   ', '+523312345678', ' +523312345678 ', '+12345678', '+123456789012345'])(
    'accepts %j',
    (phone) => {
      expect(alertPhoneError(phone)).toBeNull()
    },
  )

  it.each([
    '523312345678', // no +
    '+0523312345678', // leading 0 after +
    '+1234567', // 7 digits, too short
    '+1234567890123456', // 16 digits, too long
    '+52 33 1234 5678', // spaces
    '+52-3312345678',
  ])('rejects %j', (phone) => {
    expect(alertPhoneError(phone)).toMatch(/international format/)
  })

  it('shows the example number in the message', () => {
    expect(alertPhoneError('33 1234 5678')).toContain(ALERT_PHONE_EXAMPLE)
  })
})
