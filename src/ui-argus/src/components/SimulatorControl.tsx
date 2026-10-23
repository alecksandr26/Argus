import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '../api/client'
import { SIMULATOR_POLL_MS } from '../constants'
import {
  getSimulatorStatus,
  startSimulator,
  stopSimulator,
  type SimulatorStatus,
} from '../api/simulator'
import StatusPill from './StatusPill'
import type { Badge } from '../utils/status'

/**
 * root_admin-only on/off switch for the cloud fleet simulator (`/api/simulator`, see
 * `src/backend-argus/app/simulator_control.py`). Every session runs a fixed 10 minutes and then
 * turns itself off; the backend enforces that and a monthly quota, so this component only
 * reflects them — there's deliberately no duration input. The parent decides visibility by role;
 * the backend 403s every other role anyway.
 */

const POLL_MS = SIMULATOR_POLL_MS

const stateBadge: Record<SimulatorStatus['state'], Badge> = {
  stopped: { label: 'Off', tone: 'neutral' },
  starting: { label: 'Starting…', tone: 'warn' },
  running: { label: 'On', tone: 'good' },
}

function errorMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return 'Could not reach the backend'
  switch (err.status) {
    case 409:
      return 'The simulator is already on.'
    case 429:
      return 'Monthly simulator quota used up. It resets on the 1st.'
    case 503:
      return 'Simulator control is not configured on this deployment.'
    default:
      return err.message
  }
}

function formatCountdown(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

export default function SimulatorControl({ token }: { token: string }) {
  const [status, setStatus] = useState<SimulatorStatus | null>(null)
  const [truckCount, setTruckCount] = useState(6)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())

  const refresh = useCallback(() => {
    getSimulatorStatus(token)
      .then((s) => {
        setStatus(s)
        setError(null)
      })
      .catch((err) => setError(errorMessage(err)))
  }, [token])

  useEffect(() => {
    refresh()
  }, [refresh])

  const isOn = status?.state === 'starting' || status?.state === 'running'

  // Poll only while a session is open; nothing changes on its own while it's off.
  useEffect(() => {
    if (!isOn) return
    const id = setInterval(refresh, POLL_MS)
    return () => clearInterval(id)
  }, [isOn, refresh])

  // 1 s tick for the countdown.
  useEffect(() => {
    if (!isOn) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [isOn])

  const remainingMs = status?.auto_off_at ? +new Date(status.auto_off_at) - now : 0

  // When the countdown hits zero, re-check right away instead of waiting for the next poll.
  useEffect(() => {
    if (isOn && status?.auto_off_at && remainingMs <= 0) refresh()
  }, [isOn, status?.auto_off_at, remainingMs <= 0, refresh]) // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = async () => {
    setBusy(true)
    setError(null)
    try {
      const next = isOn ? await stopSimulator(token) : await startSimulator(truckCount, token)
      setStatus(next)
      setNow(Date.now())
    } catch (err) {
      setError(errorMessage(err))
      refresh()
    } finally {
      setBusy(false)
    }
  }

  const minutes = status?.session_minutes ?? 10
  const noQuota = !!status && !isOn && status.sessions_remaining <= 0
  const disabled = busy || !status || noQuota

  return (
    <section
      className="panel"
      aria-label="Fleet simulator"
      style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 160 }}>
        <span style={{ fontWeight: 600 }}>Fleet simulator</span>
        <span style={{ fontSize: 12, color: 'var(--text-soft)' }}>
          Runs for {minutes} minutes, then turns itself off
        </span>
      </div>

      <button
        type="button"
        role="switch"
        aria-checked={isOn}
        aria-label="Simulator on/off"
        disabled={disabled}
        onClick={toggle}
        style={{
          width: 46,
          height: 26,
          borderRadius: 13,
          border: 'none',
          padding: 3,
          cursor: disabled ? 'not-allowed' : 'pointer',
          opacity: disabled ? 0.5 : 1,
          background: isOn ? 'var(--good)' : 'var(--border-soft)',
          display: 'flex',
          justifyContent: isOn ? 'flex-end' : 'flex-start',
          transition: 'background 120ms',
        }}
      >
        <span
          style={{ width: 20, height: 20, borderRadius: '50%', background: 'var(--surface)' }}
        />
      </button>

      {status && <StatusPill badge={stateBadge[status.state]} />}

      {isOn && status?.auto_off_at && (
        <span className="mono" style={{ fontSize: 13 }} aria-label="Time until auto-off">
          Auto-off in {formatCountdown(remainingMs)}
        </span>
      )}

      {!isOn && (
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
          Trucks
          <input
            className="input"
            type="number"
            min={1}
            max={10}
            value={truckCount}
            onChange={(e) => setTruckCount(Math.min(10, Math.max(1, Number(e.target.value) || 1)))}
            style={{ width: 64 }}
          />
        </label>
      )}

      {status && (
        <span style={{ fontSize: 12, color: 'var(--text-soft)', marginLeft: 'auto' }}>
          Monthly quota: {status.sessions_remaining} of{' '}
          {Math.floor(status.quota_minutes / minutes)} sessions left
        </span>
      )}

      {error && (
        <span role="alert" style={{ fontSize: 12, color: 'var(--bad)', width: '100%' }}>
          {error}
        </span>
      )}
    </section>
  )
}
