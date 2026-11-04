import { guardianName, hasValidGuardianPhone } from '../utils/guardian'
import type { User } from '../types'

/**
 * The `<option>`s of a Guardian `<select>` (route guardian contracts G7): a "Select a guardian"
 * placeholder (value `''`), then each guardian with their phone. A guardian whose phone isn't
 * E.164 is a disabled option marked "(no valid phone)": the backend would reject them (G1), and
 * the truck couldn't text them anyway.
 */
export default function GuardianOptions({ guardians }: { guardians: User[] }) {
  return (
    <>
      <option value="" disabled>
        Select a guardian
      </option>
      {guardians.map((g) => {
        const valid = hasValidGuardianPhone(g)
        return (
          <option key={g.id_user} value={g.id_user} disabled={!valid}>
            {guardianName(g)}
            {valid ? ` · ${g.phone_number}` : ' (no valid phone)'}
          </option>
        )
      })}
    </>
  )
}
