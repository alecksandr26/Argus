import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import Icon from '../components/Icon'
import { CURRENT_USER } from '../data/fixtures'

/**
 * Mockup: "Login" artboard. No real auth yet (INTEGRATION.md gap #3) — submit
 * just routes to the dashboard. The form is controlled so the submit handler
 * has somewhere real to plug `POST /api/auth/login` in later.
 */
export default function Login() {
  const navigate = useNavigate()
  const [email, setEmail] = useState(CURRENT_USER.email)
  const [password, setPassword] = useState('demo-password')
  const [keepSignedIn, setKeepSignedIn] = useState(true)

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    // TODO(INTEGRATION.md #3): POST /api/auth/login, store session, redirect by role.
    navigate('/')
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
            style={{ marginTop: 6, padding: 12, fontSize: 14 }}
          >
            Sign in
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
