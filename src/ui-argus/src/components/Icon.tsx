/**
 * Inline SVG icon set, lifted from the mockups' hand-drawn `<svg>` paths so the
 * ported screens don't each re-paste raw path data. `currentColor` throughout —
 * colour comes from the parent's `color`.
 */

import type { CSSProperties, ReactNode } from 'react'

// `satisfies` (not a `:` annotation) so `keyof typeof PATHS` below stays the
// literal union of icon names rather than widening to `string`.
const PATHS = {
  eye: (
    <>
      <path d="M3 12s3.5-6 9-6 9 6 9 6-3.5 6-9 6-9-6-9-6Z" />
      <circle cx="12" cy="12" r="2.5" />
    </>
  ),
  'eye-brand': (
    <>
      <path d="M2 12C4.5 6.5 8 4 12 4s7.5 2.5 10 8c-2.5 5.5-6 8-10 8s-7.5-2.5-10-8Z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  list: <path d="M4 6h16M4 12h16M4 18h10" strokeLinecap="round" />,
  route: (
    <>
      <path
        d="M4 19c3-1 4-6 4-9s2-6 4-6 3 3 3 6-1 8 4 9"
        strokeLinecap="round"
      />
      <circle cx="4" cy="19" r="1.4" fill="currentColor" />
      <circle cx="19" cy="19" r="1.4" fill="currentColor" />
    </>
  ),
  truck: (
    <path
      d="M3 16V8a1 1 0 0 1 1-1h9v9M3 16h10m0 0h3.5M3 16a2 2 0 1 0 4 0m6 0a2 2 0 1 0 4 0m-4 0h4m0 0V11h-3.5L17 8"
      strokeLinejoin="round"
    />
  ),
  users: (
    <>
      <circle cx="12" cy="8" r="3.4" />
      <path
        d="M4.5 20c1.4-4 4-5.5 7.5-5.5s6.1 1.5 7.5 5.5"
        strokeLinecap="round"
      />
    </>
  ),
  lock: (
    <>
      <rect x="5" y="10" width="14" height="10" rx="1.6" />
      <path d="M8 10V7a4 4 0 1 1 8 0v3" />
    </>
  ),
  logout: (
    <path
      d="M15 17l5-5-5-5M20 12H9M12 19H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h6"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="M20 20l-3.5-3.5" strokeLinecap="round" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" strokeLinecap="round" />,
  close: <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />,
  'chevron-down': (
    <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
  ),
  'chevron-left': (
    <path d="M15 5l-7 7 7 7" strokeLinecap="round" strokeLinejoin="round" />
  ),
  'alert-triangle': (
    <path
      d="M12 9v4M12 16.5h.01M10.3 3.9 2.8 17.3A2 2 0 0 0 4.6 20.3h14.8a2 2 0 0 0 1.8-3L14.7 3.9a2 2 0 0 0-3.4 0Z"
      strokeLinejoin="round"
    />
  ),
  phone: (
    <path
      d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3 19.5 19.5 0 0 1-6-6 19.8 19.8 0 0 1-3-8.7A2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .3 2 .7 3a2 2 0 0 1-.4 2.1L8 10.3a16 16 0 0 0 5.7 5.7l1.5-1.4a2 2 0 0 1 2.1-.4c1 .3 2 .5 3 .7a2 2 0 0 1 1.7 2Z"
      strokeLinejoin="round"
    />
  ),
  building: (
    <path d="M4 19h16M6 19V9l6-4 6 4v10" strokeLinejoin="round" />
  ),
  siren: (
    <path
      d="M3 10a9 9 0 1 1 3 6.7M3 10v5M3 10h5"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  ),
  play: <path d="M8 5v14l11-7-11-7Z" fill="currentColor" stroke="none" />,
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v.01M11 12h1v4h1" strokeLinecap="round" />
    </>
  ),
  check: (
    <path
      d="M20 6 9 17l-5-5"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  ),
} satisfies Record<string, ReactNode>

export type IconName = keyof typeof PATHS

export default function Icon({
  name,
  size = 17,
  strokeWidth = 1.7,
  className,
  style,
}: {
  name: IconName
  size?: number
  strokeWidth?: number
  className?: string
  style?: CSSProperties
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      className={className}
      style={{ flexShrink: 0, ...style }}
      aria-hidden="true"
    >
      {PATHS[name]}
    </svg>
  )
}
