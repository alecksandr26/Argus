import { useEffect, useMemo, useState } from 'react'
import PageHeader from '../components/PageHeader'
import SearchBox from '../components/SearchBox'
import RecordTable, { type Column } from '../components/RecordTable'
import StatusPill from '../components/StatusPill'
import Icon from '../components/Icon'
import { useAuth } from '../context/AuthContext'
import { createUser, deleteUser, listUsers, updateUser } from '../api/users'
import { ApiError } from '../api/client'
import { relativeTime } from '../utils/format'
import { roleLabel, userStatus } from '../utils/status'
import type { Role, User } from '../types'

/**
 * "Access" — root_admin/admin user management against the real `/api/users`. root_admin sees
 * and can create/edit any role; an `admin` session is scoped server-side to guardian-role
 * accounts only (see `src/backend-argus/app/routers/users.py`'s per-route scoping) — this page
 * mirrors that scoping client-side (the role picker only ever offers `guardian` for an admin
 * actor) rather than re-deciding it, since the backend is the actual enforcement point.
 * "Deactivate" (`PUT is_active:false`) and "Delete permanently" (`DELETE`, a real hard delete)
 * are kept as two distinct, clearly labeled actions — conflating them would be an easy mistake.
 */

const ALL_ROLES: Role[] = ['root_admin', 'admin', 'guardian', 'truck_driver']

type Draft = {
  id_user: string
  email: string
  first_name: string
  last_name: string
  phone_number: string
  role: Role
  is_active: boolean
  password: string
}

export default function Access() {
  const { session } = useAuth()
  const token = session!.token
  const isRootAdmin = session!.user.role === 'root_admin'
  // An `admin` actor may only ever create/see guardian accounts — the backend already enforces
  // this; the role picker here just doesn't offer choices that would be rejected anyway.
  const assignableRoles = isRootAdmin ? ALL_ROLES : (['guardian'] as Role[])

  const [list, setList] = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [mode, setMode] = useState<'edit' | 'create' | null>(null)
  const [saving, setSaving] = useState(false)

  const blankDraft = (): Draft => ({
    id_user: '',
    email: '',
    first_name: '',
    last_name: '',
    phone_number: '',
    role: assignableRoles[0],
    is_active: true,
    password: '',
  })

  const toDraft = (u: User): Draft => ({
    id_user: u.id_user,
    email: u.email,
    first_name: u.first_name,
    last_name: u.last_name,
    phone_number: u.phone_number,
    role: u.role,
    is_active: u.is_active,
    password: '',
  })

  function reload() {
    setLoading(true)
    return listUsers(token)
      .then(setList)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load users'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return list
    return list.filter((u) =>
      `${u.first_name} ${u.last_name} ${u.email}`.toLowerCase().includes(q),
    )
  }, [list, query])

  function selectRow(u: User) {
    setDraft(toDraft(u))
    setMode('edit')
  }
  function startCreate() {
    setDraft(blankDraft())
    setMode('create')
  }
  function closePanel() {
    setDraft(null)
    setMode(null)
  }

  async function save() {
    if (!draft) return
    setSaving(true)
    setError(null)
    try {
      if (mode === 'create') {
        const created = await createUser(
          {
            email: draft.email,
            password: draft.password,
            role: draft.role,
            first_name: draft.first_name,
            last_name: draft.last_name,
            phone_number: draft.phone_number,
            is_active: draft.is_active,
          },
          token,
        )
        setList((prev) => [created, ...prev])
      } else {
        const updated = await updateUser(
          draft.id_user,
          {
            email: draft.email,
            first_name: draft.first_name,
            last_name: draft.last_name,
            phone_number: draft.phone_number,
            role: draft.role,
            is_active: draft.is_active,
            ...(draft.password ? { password: draft.password } : {}),
          },
          token,
        )
        setList((prev) => prev.map((u) => (u.id_user === updated.id_user ? updated : u)))
      }
      closePanel()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to save user')
    } finally {
      setSaving(false)
    }
  }

  async function toggleActive() {
    if (!draft) return
    setSaving(true)
    setError(null)
    try {
      const updated = await updateUser(draft.id_user, { is_active: !draft.is_active }, token)
      setList((prev) => prev.map((u) => (u.id_user === updated.id_user ? updated : u)))
      setDraft({ ...draft, is_active: updated.is_active })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to update user')
    } finally {
      setSaving(false)
    }
  }

  async function remove() {
    if (!draft) return
    if (!window.confirm(`Permanently delete ${draft.email}? This cannot be undone.`)) return
    setSaving(true)
    setError(null)
    try {
      await deleteUser(draft.id_user, token)
      setList((prev) => prev.filter((u) => u.id_user !== draft.id_user))
      closePanel()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to delete user')
    } finally {
      setSaving(false)
    }
  }

  const columns: Column<User>[] = [
    {
      header: 'Name',
      cell: (u) => (
        <span style={{ fontWeight: 600 }}>
          {u.first_name} {u.last_name}
        </span>
      ),
    },
    { header: 'Email', cell: (u) => <span className="mono">{u.email}</span> },
    { header: 'Role', cell: (u) => roleLabel[u.role] },
    {
      header: 'Status',
      cell: (u) => <StatusPill badge={userStatus[u.is_active ? 'active' : 'inactive']} />,
    },
    {
      header: 'Last login',
      cell: (u) => (
        <span style={{ color: 'var(--text-faint)' }}>
          {u.last_login ? relativeTime(u.last_login) : 'Never'}
        </span>
      ),
    },
  ]

  return (
    <div
      style={{
        display: 'flex',
        padding: '24px 28px',
        gap: 18,
        height: '100%',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          gap: 18,
          minWidth: 0,
        }}
      >
        <PageHeader
          title="Access"
          subtitle={
            isRootAdmin
              ? `${list.length} accounts`
              : `${list.length} guardian accounts you manage`
          }
          actions={
            <>
              <SearchBox value={query} onChange={setQuery} placeholder="Search name or email…" />
              <button className="btn btn--accent" onClick={startCreate}>
                <Icon name="user-plus" size={15} strokeWidth={2} />
                {isRootAdmin ? 'Add user' : 'Add guardian'}
              </button>
            </>
          }
        />

        {error && (
          <div role="alert" style={{ color: 'var(--danger, #e5484d)', fontSize: 13 }}>
            {error}
          </div>
        )}

        <RecordTable
          columns={columns}
          rows={filtered}
          getId={(u) => u.id_user}
          selectedId={draft?.id_user}
          onSelect={selectRow}
          emptyLabel={loading ? 'Loading users…' : 'No user matches the search.'}
        />
      </div>

      {draft && (
        <EditPanel
          draft={draft}
          mode={mode}
          assignableRoles={assignableRoles}
          saving={saving}
          onChange={setDraft}
          onCancel={closePanel}
          onSave={save}
          onToggleActive={toggleActive}
          onDelete={remove}
        />
      )}
    </div>
  )
}

function EditPanel({
  draft,
  mode,
  assignableRoles,
  saving,
  onChange,
  onCancel,
  onSave,
  onToggleActive,
  onDelete,
}: {
  draft: Draft
  mode: 'edit' | 'create' | null
  assignableRoles: Role[]
  saving: boolean
  onChange: (d: Draft) => void
  onCancel: () => void
  onSave: () => void
  onToggleActive: () => void
  onDelete: () => void
}) {
  const set = (patch: Partial<Draft>) => onChange({ ...draft, ...patch })

  return (
    <div
      className="panel"
      style={{
        width: 360,
        flexShrink: 0,
        padding: 20,
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
        overflowY: 'auto',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 14, fontWeight: 600 }}>
          {mode === 'create' ? 'New user' : 'Edit user'}
        </span>
        <button
          onClick={onCancel}
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-faint)', padding: 0 }}
          aria-label="Close"
        >
          <Icon name="close" size={16} strokeWidth={1.8} />
        </button>
      </div>

      <div style={{ display: 'flex', gap: 10 }}>
        <label className="field" style={{ flex: 1 }}>
          <span className="field__label">First name</span>
          <input
            className="input"
            value={draft.first_name}
            onChange={(e) => set({ first_name: e.target.value })}
          />
        </label>
        <label className="field" style={{ flex: 1 }}>
          <span className="field__label">Last name</span>
          <input
            className="input"
            value={draft.last_name}
            onChange={(e) => set({ last_name: e.target.value })}
          />
        </label>
      </div>
      <label className="field">
        <span className="field__label">Email</span>
        <input
          className="input mono"
          type="email"
          value={draft.email}
          onChange={(e) => set({ email: e.target.value })}
        />
      </label>
      <label className="field">
        <span className="field__label">Phone</span>
        <input
          className="input mono"
          value={draft.phone_number}
          onChange={(e) => set({ phone_number: e.target.value })}
        />
      </label>
      <label className="field">
        <span className="field__label">Role</span>
        <select
          className="input"
          value={draft.role}
          disabled={assignableRoles.length === 1}
          onChange={(e) => set({ role: e.target.value as Role })}
        >
          {assignableRoles.map((r) => (
            <option key={r} value={r}>
              {roleLabel[r]}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span className="field__label">
          {mode === 'create' ? 'Password' : 'New password (leave blank to keep current)'}
        </span>
        <input
          className="input mono"
          type="password"
          value={draft.password}
          onChange={(e) => set({ password: e.target.value })}
        />
      </label>
      {mode === 'create' && (
        <label
          style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5, color: 'var(--text-soft)' }}
        >
          <input
            type="checkbox"
            checked={draft.is_active}
            onChange={(e) => set({ is_active: e.target.checked })}
            style={{ accentColor: 'var(--accent)', width: 14, height: 14 }}
          />
          Active from creation
        </label>
      )}

      <div style={{ display: 'flex', gap: 10, marginTop: 'auto' }}>
        <button className="btn btn--block" onClick={onCancel}>
          Cancel
        </button>
        <button className="btn btn--accent btn--block" onClick={onSave} disabled={saving}>
          {saving ? 'Saving…' : mode === 'create' ? 'Create user' : 'Save changes'}
        </button>
      </div>

      {mode === 'edit' && (
        <>
          <div style={{ height: 1, background: 'var(--border-soft)' }} />
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn btn--block" onClick={onToggleActive} disabled={saving}>
              {draft.is_active ? 'Deactivate' : 'Reactivate'}
            </button>
            <button className="btn btn--danger btn--block" onClick={onDelete} disabled={saving}>
              <Icon name="trash" size={14} />
              Delete permanently
            </button>
          </div>
        </>
      )}
    </div>
  )
}
