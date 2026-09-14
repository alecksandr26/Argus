import { useState, type FormEvent } from 'react'
import { useLocation, useNavigate, type Location } from 'react-router-dom'
import Icon from '../components/Icon'
import { useAuth } from '../context/AuthContext'
import { ApiError } from '../api/client'

/**
 * Real login: submits to `POST /api/auth/login` via `useAuth().login()` (which pre-hashes the
 * password client-side, see `src/utils/crypto.ts`). On success, redirects back to whatever page
 * `ProtectedRoute` originally bounced the user from (`location.state.from`), falling back to `/`
 * — there's no per-role landing page to redirect to yet, so `/` isn't a shortcut, it's the only
 * option that exists.
 */
export default function Login() {
  const navigate = useNavigate()
  const location = useLocation()
  const { login } = useAuth()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [keepSignedIn, setKeepSignedIn] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      await login(email, password)
      const from = (location.state as { from?: Location } | null)?.from
      navigate(from ? `${from.pathname}${from.search}` : '/', { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not sign in. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        height: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        position: 'relative',
        overflow: 'hidden',
        background: `
          radial-gradient(1100px 600px at 18% -10%, oklch(0.78 0.13 215 / 0.08), transparent 60%),
          radial-gradient(900px 500px at 100% 110%, oklch(0.78 0.13 215 / 0.05), transparent 60%),
          var(--bg)`,
      }}
    >
      <div
        style={{
          position: 'absolute',
          inset: 0,
          backgroundImage:
            'linear-gradient(var(--border-soft) 1px, transparent 1px), linear-gradient(90deg, var(--border-soft) 1px, transparent 1px)',
          backgroundSize: '64px 64px',
          opacity: 0.35,
        }}
      />

      <form
        onSubmit={onSubmit}
        className="panel"
        style={{
          width: 400,
          padding: '38px 34px 30px',
          position: 'relative',
          boxShadow: '0 30px 60px -20px oklch(0 0 0 / 0.5)',
        }}
      >
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 10,
            marginBottom: 26,
          }}
        >
          <Icon
            name="eye-brand"
            size={34}
            strokeWidth={1.6}
            style={{ color: 'var(--accent)' }}
          />
          <div
            style={{
              fontFamily: 'var(--font-display)',
              fontWeight: 700,
              fontSize: 22,
              letterSpacing: '0.02em',
            }}
          >
            ARGUS
          </div>
          <div
            style={{
              fontSize: 12.5,
              color: 'var(--text-faint)',
              textAlign: 'center',
            }}
          >
            Fatigue monitoring &amp; vehicle safety system
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <label className="field">
            <span className="field__label">Email address</span>
            <input
              className="input"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <label className="field">
            <span className="field__label">Password</span>
            <input
              className="input"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>

          {error && (
            <div
              role="alert"
              style={{
                fontSize: 12.5,
                color: 'var(--danger, #e5484d)',
                background: 'color-mix(in oklch, var(--danger, #e5484d) 12%, transparent)',
                border: '1px solid color-mix(in oklch, var(--danger, #e5484d) 35%, transparent)',
                borderRadius: 8,
                padding: '8px 10px',
              }}
            >
              {error}
            </div>
          )}

          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginTop: -4,
            }}
          >
            <label
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                fontSize: 12,
                color: 'var(--text-faint)',
              }}
            >
              <input
                type="checkbox"
                checked={keepSignedIn}
                onChange={(e) => setKeepSignedIn(e.target.checked)}
                style={{ accentColor: 'var(--accent)', width: 14, height: 14 }}
              />
              Keep me signed in
            </label>
            <a href="#" style={{ fontSize: 12 }}>
              Forgot your password?
            </a>
          </div>

          <button
            type="submit"
            className="btn btn--accent btn--block"
            disabled={loading}
            style={{ marginTop: 6, padding: 12, fontSize: 14 }}
          >
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </div>

        <div
          style={{
            marginTop: 22,
            paddingTop: 16,
            borderTop: '1px solid var(--border-soft)',
            display: 'flex',
            alignItems: 'flex-start',
            gap: 8,
          }}
        >
          <Icon
            name="info"
            size={14}
            style={{ color: 'var(--text-faint)', marginTop: 1 }}
          />
          <span
            style={{
              fontSize: 11.5,
              color: 'var(--text-faint)',
              lineHeight: 1.5,
            }}
          >
            Authorized personnel only — Control Tower and Fleet Administration.
            Your account role decides which panels you see after signing in.
          </span>
        </div>
      </form>

      <span
        style={{
          position: 'absolute',
          bottom: 22,
          fontSize: 11,
          color: 'var(--text-faint)',
        }}
      >
        Argus © 2026 — A preventive safety layer, not a replacement for the driver
      </span>
    </div>
  )
}
