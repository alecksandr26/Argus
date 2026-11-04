import { useEffect, useState } from 'react'
import { getHealth } from '../api/health'
import { UI_VERSION, versionsDiffer } from '../utils/version'

/**
 * Warns when this UI and the backend were built from different commits (contracts G6). A UI
 * deployed ahead of its backend calls endpoints or reads fields the backend doesn't have yet,
 * which looks like a broken feature (e.g. a rotate-key answer without the guardian phone). A
 * 'dev' build on either side never warns, and a local 'dev' UI doesn't even ask.
 */
export default function VersionWarning({ uiVersion = UI_VERSION }: { uiVersion?: string }) {
  const [backend, setBackend] = useState<string | null>(null)

  useEffect(() => {
    if (uiVersion === 'dev') return
    let cancelled = false
    getHealth()
      .then((h) => {
        if (!cancelled) setBackend(typeof h?.version === 'string' ? h.version : null)
      })
      .catch(() => {
        // An unreachable backend shows up everywhere else; this check just stays quiet.
      })
    return () => {
      cancelled = true
    }
  }, [uiVersion])

  if (!versionsDiffer(uiVersion, backend)) return null
  return (
    <div
      role="status"
      data-testid="version-warning"
      style={{ fontSize: 12, color: 'var(--warn, #f5a524)' }}
    >
      This UI (<span className="mono">{uiVersion}</span>) and the backend (
      <span className="mono">{backend}</span>) are different versions — redeploy both.
    </div>
  )
}
