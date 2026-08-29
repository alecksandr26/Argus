import { useMemo, useState } from 'react'
import PageHeader from '../components/PageHeader'
import SearchBox from '../components/SearchBox'
import RecordTable, { type Column } from '../components/RecordTable'
import StatusPill from '../components/StatusPill'
import Icon from '../components/Icon'
import { drivers as seedDrivers } from '../data/fixtures'
import { daysUntil, shortDate } from '../utils/format'
import { driverStatus } from '../utils/status'
import type { Driver, DriverStatus } from '../types'

/**
 * Mockup: "Administration — Drivers". CRUD over the `Driver` entity in local
 * state, same pattern as Fleet. Wiring target: `GET/POST/PUT/DELETE /api/drivers`.
 */

const STATUS_OPTIONS: DriverStatus[] = [
  'on_route',
  'on_route_alert',
  'resting',
  'inactive',
]

type Draft = {
  id_driver: string
  first_name: string
  last_name: string
  license_number: string
  license_expiration: string
  phone_number: string
  emergency_contact_name: string
  emergency_contact_phone: string
  blood_type: string
  operative_status: DriverStatus
}

const blankDraft = (): Draft => ({
  id_driver: '',
  first_name: '',
  last_name: '',
  license_number: '',
  license_expiration: '',
  phone_number: '',
  emergency_contact_name: '',
  emergency_contact_phone: '',
  blood_type: '',
  operative_status: 'inactive',
})

const toDraft = (d: Driver): Draft => ({
  id_driver: d.id_driver,
  first_name: d.first_name,
  last_name: d.last_name,
  license_number: d.license_number,
  license_expiration: d.license_expiration,
  phone_number: d.phone_number,
  emergency_contact_name: d.emergency_contact_name,
  emergency_contact_phone: d.emergency_contact_phone,
  blood_type: d.blood_type,
  operative_status: d.operative_status,
})

export default function Drivers() {
  const [list, setList] = useState<Driver[]>(seedDrivers)
  const [query, setQuery] = useState('')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [mode, setMode] = useState<'edit' | 'create' | null>(null)

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return list
    return list.filter((d) =>
      `${d.first_name} ${d.last_name} ${d.license_number}`
        .toLowerCase()
        .includes(q),
    )
  }, [list, query])

  const activeCount = list.filter(
    (d) =>
      d.operative_status === 'on_route' ||
      d.operative_status === 'on_route_alert',
  ).length

  function selectRow(d: Driver) {
    setDraft(toDraft(d))
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
  function save() {
    if (!draft) return
    const now = new Date().toISOString()
    if (mode === 'create') {
      const id = `drv-${Math.random().toString(36).slice(2, 7)}`
      setList((prev) => [
        { ...draft, id_driver: id, created_at: now, updated_at: now },
        ...prev,
      ])
    } else {
      setList((prev) =>
        prev.map((d) =>
          d.id_driver === draft.id_driver
            ? { ...d, ...draft, updated_at: now }
            : d,
        ),
      )
    }
    closePanel()
  }

  const columns: Column<Driver>[] = [
    {
      header: 'Name',
      cell: (d) => (
        <span style={{ fontWeight: 600 }}>
          {d.first_name} {d.last_name}
        </span>
      ),
    },
    {
      header: 'License',
      cell: (d) => <span className="mono">{d.license_number}</span>,
    },
    {
      header: 'Expires',
      cell: (d) => {
        const days = daysUntil(d.license_expiration)
        const soon = days <= 30
        return (
          <span style={{ color: soon ? 'var(--warn)' : 'var(--text)' }}>
            {shortDate(d.license_expiration)}
            {soon && days >= 0 ? ' · expiring soon' : ''}
            {days < 0 ? ' · expired' : ''}
          </span>
        )
      },
    },
    {
      header: 'Emergency contact',
      cell: (d) => (
        <span style={{ color: 'var(--text-soft)' }}>
          {d.emergency_contact_name} · {d.emergency_contact_phone}
        </span>
      ),
    },
    {
      header: 'Blood type',
      cell: (d) => <span className="mono">{d.blood_type}</span>,
    },
    {
      header: 'Status',
      cell: (d) => <StatusPill badge={driverStatus[d.operative_status]} />,
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
          title="Drivers"
          subtitle={`${list.length} drivers registered · ${activeCount} on route`}
          actions={
            <>
              <SearchBox
                value={query}
                onChange={setQuery}
                placeholder="Search name or license…"
              />
              <button className="btn btn--accent" onClick={startCreate}>
                <Icon name="plus" size={15} strokeWidth={2} />
                Add driver
              </button>
            </>
          }
        />

        <RecordTable
          columns={columns}
          rows={filtered}
          getId={(d) => d.id_driver}
          selectedId={draft?.id_driver}
          onSelect={selectRow}
          emptyLabel="No driver matches the search."
        />
      </div>

      {draft && (
        <EditPanel
          draft={draft}
          mode={mode}
          onChange={setDraft}
          onCancel={closePanel}
          onSave={save}
        />
      )}
    </div>
  )
}

function EditPanel({
  draft,
  mode,
  onChange,
  onCancel,
  onSave,
}: {
  draft: Draft
  mode: 'edit' | 'create' | null
  onChange: (d: Draft) => void
  onCancel: () => void
  onSave: () => void
}) {
  const set = (patch: Partial<Draft>) => onChange({ ...draft, ...patch })
  const initials = (draft.first_name[0] ?? '') + (draft.last_name[0] ?? '')

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
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <span style={{ fontSize: 14, fontWeight: 600 }}>
          {mode === 'create' ? 'New driver' : 'Edit driver'}
        </span>
        <button
          onClick={onCancel}
          style={{
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            color: 'var(--text-faint)',
            padding: 0,
          }}
          aria-label="Close"
        >
          <Icon name="close" size={16} strokeWidth={1.8} />
        </button>
      </div>

      {mode === 'edit' && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            marginTop: -8,
          }}
        >
          <span
            style={{
              width: 36,
              height: 36,
              borderRadius: '50%',
              background: 'var(--surface-3)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 13,
              fontWeight: 600,
              color: 'var(--accent)',
            }}
          >
            {initials.toUpperCase()}
          </span>
          <span
            className="mono"
            style={{ fontSize: 11.5, color: 'var(--text-faint)' }}
          >
            {draft.first_name} {draft.last_name} · {draft.license_number}
          </span>
        </div>
      )}

      <div style={{ display: 'flex', gap: 10 }}>
        <label className="field" style={{ flex: 1 }}>
          <span className="field__label">First name(s)</span>
          <input
            className="input"
            value={draft.first_name}
            onChange={(e) => set({ first_name: e.target.value })}
          />
        </label>
        <label className="field" style={{ flex: 1 }}>
          <span className="field__label">Last name(s)</span>
          <input
            className="input"
            value={draft.last_name}
            onChange={(e) => set({ last_name: e.target.value })}
          />
        </label>
      </div>
      <div style={{ display: 'flex', gap: 10 }}>
        <label className="field" style={{ flex: 1 }}>
          <span className="field__label">License no.</span>
          <input
            className="input mono"
            value={draft.license_number}
            onChange={(e) => set({ license_number: e.target.value })}
          />
        </label>
        <label className="field" style={{ flex: 1 }}>
          <span className="field__label">Expires</span>
          <input
            className="input mono"
            type="date"
            value={draft.license_expiration}
            onChange={(e) => set({ license_expiration: e.target.value })}
          />
        </label>
      </div>
      <label className="field">
        <span className="field__label">Phone</span>
        <input
          className="input mono"
          value={draft.phone_number}
          onChange={(e) => set({ phone_number: e.target.value })}
        />
      </label>

      <div style={{ height: 1, background: 'var(--border-soft)' }} />
      <span
        style={{
          fontSize: 11.5,
          fontWeight: 600,
          color: 'var(--text-soft)',
        }}
      >
        Emergency contact
      </span>
      <div style={{ display: 'flex', gap: 10 }}>
        <label className="field" style={{ flex: 1 }}>
          <span className="field__label">Name</span>
          <input
            className="input"
            value={draft.emergency_contact_name}
            onChange={(e) =>
              set({ emergency_contact_name: e.target.value })
            }
          />
        </label>
        <label className="field" style={{ flex: 1 }}>
          <span className="field__label">Phone</span>
          <input
            className="input mono"
            value={draft.emergency_contact_phone}
            onChange={(e) =>
              set({ emergency_contact_phone: e.target.value })
            }
          />
        </label>
      </div>
      <div style={{ display: 'flex', gap: 10 }}>
        <label className="field" style={{ width: 90 }}>
          <span className="field__label">Blood</span>
          <input
            className="input mono"
            value={draft.blood_type}
            onChange={(e) => set({ blood_type: e.target.value })}
          />
        </label>
        <label className="field" style={{ flex: 1 }}>
          <span className="field__label">Status</span>
          <select
            className="input"
            value={draft.operative_status}
            onChange={(e) =>
              set({ operative_status: e.target.value as DriverStatus })
            }
          >
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {driverStatus[s].label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div style={{ display: 'flex', gap: 10, marginTop: 'auto' }}>
        <button className="btn btn--block" onClick={onCancel}>
          Cancel
        </button>
        <button className="btn btn--accent btn--block" onClick={onSave}>
          {mode === 'create' ? 'Create driver' : 'Save changes'}
        </button>
      </div>
    </div>
  )
}
