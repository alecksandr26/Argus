/**
 * Small formatting helpers shared across screens. Relative times default to the real wall
 * clock (`new Date()`) now that every screen fetches live backend data — the optional `now`
 * param stays purely for tests to pin a fixed reference time.
 */

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
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

/** "06:30" — or "Yesterday 22:00" / "Aug 23 22:00" when not today relative to `now`. */
export function clock(iso: string | null, now: Date = new Date()): string {
  if (!iso) return '—'
  const d = new Date(iso)
  const sameDay = d.toDateString() === now.toDateString()
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  if (sameDay) return clockFmt.format(d)
  if (d.toDateString() === yesterday.toDateString())
    return `Yesterday ${clockFmt.format(d)}`
  return `${shortDate(iso)} ${clockFmt.format(d)}`
}

const dateFmt = new Intl.DateTimeFormat('en-US', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
})

/** "Nov 18, 2026" */
export function shortDate(iso: string): string {
  return dateFmt.format(new Date(iso))
}

const dayFmt = new Intl.DateTimeFormat('en-US', {
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
