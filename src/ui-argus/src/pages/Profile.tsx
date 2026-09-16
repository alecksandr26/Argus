import { useEffect, useState } from 'react'
import PageHeader from '../components/PageHeader'
import Icon from '../components/Icon'
import { useAuth } from '../context/AuthContext'
import { getMe, updateMe } from '../api/me'
import { ApiError } from '../api/client'
import { roleLabel } from '../utils/status'
import type { User } from '../types'

/**
 * Self-service "My profile" — available to every authenticated role via `GET`/`PUT
 * /api/auth/me`. Deliberately has no role/active-status field anywhere in this page: the
 * backend schema (`MeUpdate`) doesn't even accept them, so changing your own role or
 * reactivating yourself isn't just hidden here, it's impossible through this endpoint —
 * that stays root_admin's (or, for guardians, a scoped admin's) job via the Access panel.
 */
export default function Profile() {
  const { session, updateSessionUser } = useAuth()
  const token = session!.token

  const [me, setMe] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [saving, setSaving] = useState(false)

  const [email, setEmail] = useState('')
  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [phone, setPhone] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')

  useEffect(() => {
    let cancelled = false
    getMe(token)
      .then((u) => {
        if (cancelled) return
        setMe(u)
        setEmail(u.email)
        setFirstName(u.first_name)
        setLastName(u.last_name)
        setPhone(u.phone_number)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : 'Failed to load profile')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [token])

  async function save() {
    if (password && password !== confirmPassword) {
      setError('Passwords do not match.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const updated = await updateMe(
        {
          email,
          first_name: firstName,
          last_name: lastName,
          phone_number: phone,
          ...(password ? { password } : {}),
        },
        token,
      )
      setMe(updated)
      updateSessionUser({
        email: updated.email,
        first_name: updated.first_name,
        last_name: updated.last_name,
      })
      setPassword('')
      setConfirmPassword('')
      setSaved(true)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to save profile')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div style={{ padding: '24px 28px', color: 'var(--text-faint)', fontSize: 13 }}>
        Loading profile…
      </div>
    )
  }

  return (
    <div style={{ padding: '24px 28px', maxWidth: 480, display: 'flex', flexDirection: 'column', gap: 18 }}>
      <PageHeader
        title="My profile"
        subtitle={me ? roleLabel[me.role] : undefined}
      />

      {error && (
        <div role="alert" style={{ color: 'var(--danger, #e5484d)', fontSize: 13 }}>
          {error}
        </div>
      )}

      <div className="panel" style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ display: 'flex', gap: 10 }}>
          <label className="field" style={{ flex: 1 }}>
            <span className="field__label">First name</span>
            <input
              className="input"
              value={firstName}
              onChange={(e) => {
                setFirstName(e.target.value)
                setSaved(false)
              }}
            />
          </label>
          <label className="field" style={{ flex: 1 }}>
            <span className="field__label">Last name</span>
            <input
              className="input"
              value={lastName}
              onChange={(e) => {
                setLastName(e.target.value)
                setSaved(false)
              }}
            />
          </label>
        </div>
        <label className="field">
          <span className="field__label">Email</span>
          <input
            className="input mono"
            type="email"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value)
              setSaved(false)
            }}
          />
        </label>
        <label className="field">
          <span className="field__label">Phone</span>
          <input
            className="input mono"
            value={phone}
            onChange={(e) => {
              setPhone(e.target.value)
              setSaved(false)
            }}
          />
        </label>

        <div style={{ height: 1, background: 'var(--border-soft)' }} />
        <span style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--text-soft)' }}>
          Change password
        </span>
        <label className="field">
          <span className="field__label">New password</span>
          <input
            className="input mono"
            type="password"
            placeholder="Leave blank to keep current password"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value)
              setSaved(false)
            }}
          />
        </label>
        <label className="field">
          <span className="field__label">Confirm new password</span>
          <input
            className="input mono"
            type="password"
            value={confirmPassword}
            onChange={(e) => {
              setConfirmPassword(e.target.value)
              setSaved(false)
            }}
          />
        </label>

        <button
          className="btn btn--accent btn--block"
          style={{ marginTop: 6 }}
          onClick={save}
          disabled={saving}
        >
          {saving ? (
            'Saving…'
          ) : saved ? (
            <>
              <Icon name="check" size={15} strokeWidth={2} /> Saved
            </>
          ) : (
            'Save changes'
          )}
        </button>
      </div>
    </div>
  )
}
