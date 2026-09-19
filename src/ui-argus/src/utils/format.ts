/**
 * Small formatting helpers shared across screens. Relative times default to the real wall
 * clock (`new Date()`) now that every screen fetches live backend data — the optional `now`
 * param stays purely for tests to pin a fixed reference time.
 *
 * Every wall-clock/date formatter here is pinned to `America/Mexico_City` rather than left to
 * the viewer's own browser (or, in a test/CI context, the host machine's) timezone — Argus is
 * exclusively a Mexico-operations system, so every actor (guardian, admin, driver) reasons about
 * Mexico local time regardless of where the browser or server physically sits. `relativeTime`/
 * `daysUntil` don't need this: they're pure epoch-millisecond deltas, never a formatted wall-clock
 * value, so they're correct in any timezone already.
 */

const MEXICO_TZ = 'America/Mexico_City'

export function relativeTime(iso: string, now: Date = new Date()): string {
  const deltaMs = now.getTime() - new Date(iso).getTime()
  const s = Math.round(deltaMs / 1000)
  if (s < 0) return 'in the future'
  if (s < 60) return `${s}s ago`
  const m = Math.round(s / 60)
  if (m < 60) return `${m} min ago`
  const h = Math.floor(m / 60)
  if (h < 24) {
    const rem = m % 60
    return rem ? `${h}h ${rem}m ago` : `${h}h ago`
  }
  const d = Math.floor(h / 24)
  return `${d}d ago`
}

const clockFmt = new Intl.DateTimeFormat('en-US', {
  timeZone: MEXICO_TZ,
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

// "YYYY-MM-DD" in Mexico City's own calendar, regardless of the host machine's timezone —
// en-CA's format is the one built-in Intl locale that gives that ordering unambiguously, used
// here purely as a comparable key, never displayed.
const mexicoDateKeyFmt = new Intl.DateTimeFormat('en-CA', {
  timeZone: MEXICO_TZ,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

function mexicoDateKey(d: Date): string {
  return mexicoDateKeyFmt.format(d)
}

/** "06:30" — or "Yesterday 22:00" / "Aug 23 22:00" when not today relative to `now`, all
 * judged by Mexico City's own calendar day, not the host machine's. */
export function clock(iso: string | null, now: Date = new Date()): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (mexicoDateKey(d) === mexicoDateKey(now)) return clockFmt.format(d)
  const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000)
  if (mexicoDateKey(d) === mexicoDateKey(yesterday))
    return `Yesterday ${clockFmt.format(d)}`
  return `${shortDate(iso)} ${clockFmt.format(d)}`
}

const dateFmt = new Intl.DateTimeFormat('en-US', {
  timeZone: MEXICO_TZ,
  day: '2-digit',
  month: 'short',
  year: 'numeric',
})

/** "Nov 18, 2026" */
export function shortDate(iso: string): string {
  return dateFmt.format(new Date(iso))
}

const dayFmt = new Intl.DateTimeFormat('en-US', {
  timeZone: MEXICO_TZ,
  weekday: 'long',
  day: 'numeric',
  month: 'long',
})

/** "Monday, August 24" */
export function longDay(d: Date = new Date()): string {
  return dayFmt.format(d)
}

export function pct(n: number): string {
  return `${Math.round(n * 100)}%`
}

/** Days until an ISO date, relative to `now`. Negative = past. */
export function daysUntil(iso: string, now: Date = new Date()): number {
  return Math.round(
    (new Date(iso).getTime() - now.getTime()) / (1000 * 60 * 60 * 24),
  )
}
