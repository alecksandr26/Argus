import type { Badge, Tone } from '../utils/status'

const toneClass: Record<Tone, string> = {
  good: 'pill--good',
  warn: 'pill--warn',
  bad: 'pill--bad',
  neutral: '',
}

/** Renders a `Badge` from `src/utils/status.ts` as the mockups' rounded status chip. */
export default function StatusPill({ badge }: { badge: Badge }) {
  return <span className={`pill ${toneClass[badge.tone]}`}>{badge.label}</span>
}
