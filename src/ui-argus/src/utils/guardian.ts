/**
 * Route guardians (route guardian contracts G1/G4/G7): the `guardian`-role user watching a route,
 * whose phone the truck texts on every critical alert.
 */

import { isActiveRoute } from './status'
import { isE164Phone } from './phone'
import type { GuardianSummary, Route, User } from '../types'

/** `"Ana López"`. */
export function guardianName(g: Pick<GuardianSummary, 'first_name' | 'last_name'>): string {
  return `${g.first_name} ${g.last_name}`.trim()
}

/** `"Ana López · +523312345678"`, or `"No guardian"`. */
export function guardianLabel(g: GuardianSummary | null): string {
  if (!g) return 'No guardian'
  return g.phone_number ? `${guardianName(g)} · ${g.phone_number}` : guardianName(g)
}

/** A route whose guardian can still be assigned or changed: not completed or stopped (the backend
 * answers 409 `"route is finished"` otherwise). Also the set the "no guardian" banner counts. */
export function isOpenRoute(r: Route): boolean {
  return r.operative_status === 'scheduled' || isActiveRoute(r.operative_status)
}

/** The users a route can be given as guardian: active `guardian`-role accounts, by name. An
 * `admin` caller's `GET /api/users` already holds only guardians; a root_admin's holds everyone,
 * so the filter matters there. */
export function selectableGuardians(users: User[]): User[] {
  return users
    .filter((u) => u.role === 'guardian' && u.is_active)
    .sort((a, b) => guardianName(a).localeCompare(guardianName(b)))
}

/** Whether a guardian can be assigned: the backend rejects one without an E.164 phone (G1). */
export function hasValidGuardianPhone(u: Pick<User, 'phone_number'>): boolean {
  return isE164Phone(u.phone_number)
}
