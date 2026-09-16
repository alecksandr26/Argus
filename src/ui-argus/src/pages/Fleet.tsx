import { useEffect, useMemo, useState } from 'react'
import PageHeader from '../components/PageHeader'
import SearchBox from '../components/SearchBox'
import RecordTable, { type Column } from '../components/RecordTable'
import StatusPill from '../components/StatusPill'
import Icon from '../components/Icon'
import { useAuth } from '../context/AuthContext'
import { createTruck, listTrucks, updateTruck } from '../api/trucks'
import { ApiError } from '../api/client'
import { relativeTime } from '../utils/format'
import { truckStatus } from '../utils/status'
import type { Truck, TruckStatus } from '../types'

/**
 * "Administration — Fleet". CRUD over the `Truck` entity against the real
 * `GET/POST/PUT /api/trucks`. Write access (add/edit) is `root_admin`/`admin` only — a
 * `guardian` still sees this screen (INTEGRATION.md), just in a read-only view: the panel
 * opens for browsing but its inputs are disabled and there's no Save button.
 */

const STATUS_OPTIONS: TruckStatus[] = [
  'active',
  'alert',
  'maintenance',
  'inactive',
]

type Draft = {
  id_truck: string
  plate_number: string
  brand: string
  model: string
  company_number: string
  raspberry_pi_mac: string
  esp32_id: string
  operative_status: TruckStatus
}

const blankDraft = (): Draft => ({
  id_truck: '',
  plate_number: '',
  brand: '',
  model: '',
  company_number: '',
  raspberry_pi_mac: '',
  esp32_id: '',
  operative_status: 'inactive',
})

const toDraft = (t: Truck): Draft => ({
  id_truck: t.id_truck,
  plate_number: t.plate_number,
  brand: t.brand,
  model: t.model,
  company_number: t.company_number,
  raspberry_pi_mac: t.raspberry_pi_mac ?? '',
  esp32_id: t.esp32_id ?? '',
  operative_status: t.operative_status,
})

export default function Fleet() {
  const { session } = useAuth()
  const token = session!.token
  const canWrite = session!.user.role === 'root_admin' || session!.user.role === 'admin'

  const [list, setList] = useState<Truck[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [mode, setMode] = useState<'edit' | 'create' | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    listTrucks(token)
      .then((trucks) => {
        if (!cancelled) setList(trucks)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : 'Failed to load fleet')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [token])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return list
    return list.filter((t) =>
      [t.plate_number, t.brand, t.model, t.company_number]
        .join(' ')
        .toLowerCase()
        .includes(q),
    )
  }, [list, query])

  const activeCount = list.filter(
    (t) => t.operative_status === 'active' || t.operative_status === 'alert',
  ).length

  function selectRow(t: Truck) {
    setDraft(toDraft(t))
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
    const input = {
      plate_number: draft.plate_number,
      brand: draft.brand,
      model: draft.model,
      company_number: draft.company_number,
      raspberry_pi_mac: draft.raspberry_pi_mac || null,
      esp32_id: draft.esp32_id || null,
      operative_status: draft.operative_status,
    }
    try {
      if (mode === 'create') {
        const created = await createTruck(input, token)
        setList((prev) => [created, ...prev])
      } else {
        const updated = await updateTruck(draft.id_truck, input, token)
        setList((prev) => prev.map((t) => (t.id_truck === updated.id_truck ? updated : t)))
      }
      closePanel()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to save truck')
    } finally {
      setSaving(false)
    }
  }

  const columns: Column<Truck>[] = [
    {
      header: 'Plate',
      cell: (t) => (
        <span className="mono" style={{ fontWeight: 600 }}>
          {t.plate_number}
        </span>
      ),
    },
    { header: 'Make / Model', cell: (t) => `${t.brand} ${t.model}` },
    {
      header: 'Unit no.',
      cell: (t) => <span className="mono">{t.company_number}</span>,
    },
    {
      header: 'Devices',
      cell: (t) =>
        t.raspberry_pi_mac ? (
          <span
            className="mono"
            style={{ fontSize: 11, color: 'var(--text-soft)' }}
          >
            RPi {t.raspberry_pi_mac.slice(0, 8)} · {t.esp32_id}
          </span>
        ) : (
          <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>
            Not linked
          </span>
        ),
    },
    {
      header: 'Status',
      cell: (t) => <StatusPill badge={truckStatus[t.operative_status]} />,
    },
    {
      header: 'Updated',
      cell: (t) => (
        <span style={{ color: 'var(--text-faint)' }}>
          {relativeTime(t.updated_at)}
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
          title="Fleet"
          subtitle={`${list.length} trucks registered · ${activeCount} active`}
          actions={
            <>
              <SearchBox
                value={query}
                onChange={setQuery}
                placeholder="Search plate or model…"
              />
              {canWrite && (
                <button className="btn btn--accent" onClick={startCreate}>
                  <Icon name="plus" size={15} strokeWidth={2} />
                  Add truck
                </button>
              )}
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
          getId={(t) => t.id_truck}
          selectedId={draft?.id_truck}
          onSelect={selectRow}
          emptyLabel={loading ? 'Loading fleet…' : 'No truck matches the search.'}
        />
      </div>

      {draft && (
        <EditPanel
          draft={draft}
          mode={mode}
          readOnly={!canWrite}
          saving={saving}
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
  readOnly,
  saving,
  onChange,
  onCancel,
  onSave,
}: {
  draft: Draft
  mode: 'edit' | 'create' | null
  readOnly: boolean
  saving: boolean
  onChange: (d: Draft) => void
  onCancel: () => void
  onSave: () => void
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
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <span style={{ fontSize: 14, fontWeight: 600 }}>
          {readOnly ? 'View truck' : mode === 'create' ? 'New truck' : 'Edit truck'}
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
            fontSize: 11.5,
            color: 'var(--text-faint)',
            marginTop: -10,
          }}
          className="mono"
        >
          {draft.plate_number} · {draft.company_number}
        </div>
      )}

      <label className="field">
        <span className="field__label">Plate</span>
        <input
          className="input mono"
          value={draft.plate_number}
          disabled={readOnly}
          onChange={(e) => set({ plate_number: e.target.value })}
        />
      </label>
      <div style={{ display: 'flex', gap: 10 }}>
        <label className="field" style={{ flex: 1 }}>
          <span className="field__label">Make</span>
          <input
            className="input"
            value={draft.brand}
            disabled={readOnly}
            onChange={(e) => set({ brand: e.target.value })}
          />
        </label>
        <label className="field" style={{ flex: 1 }}>
          <span className="field__label">Model</span>
          <input
            className="input"
            value={draft.model}
            disabled={readOnly}
            onChange={(e) => set({ model: e.target.value })}
          />
        </label>
      </div>
      <label className="field">
        <span className="field__label">Unit no.</span>
        <input
          className="input mono"
          value={draft.company_number}
          disabled={readOnly}
          onChange={(e) => set({ company_number: e.target.value })}
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
        Onboard device
      </span>
      <label className="field">
        <span className="field__label">Raspberry Pi MAC</span>
        <input
          className="input mono"
          placeholder="B8:27:EB:…"
          value={draft.raspberry_pi_mac}
          disabled={readOnly}
          onChange={(e) => set({ raspberry_pi_mac: e.target.value })}
        />
      </label>
      <label className="field">
        <span className="field__label">ESP32 ID</span>
        <input
          className="input mono"
          placeholder="ESP32-…"
          value={draft.esp32_id}
          disabled={readOnly}
          onChange={(e) => set({ esp32_id: e.target.value })}
        />
      </label>

      <label className="field">
        <span className="field__label">Operative status</span>
        <select
          className="input"
          value={draft.operative_status}
          disabled={readOnly}
          onChange={(e) =>
            set({ operative_status: e.target.value as TruckStatus })
          }
        >
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {truckStatus[s].label}
            </option>
          ))}
        </select>
      </label>

      <div style={{ display: 'flex', gap: 10, marginTop: 'auto' }}>
        <button className="btn btn--block" onClick={onCancel}>
          {readOnly ? 'Close' : 'Cancel'}
        </button>
        {!readOnly && (
          <button className="btn btn--accent btn--block" onClick={onSave} disabled={saving}>
            {saving ? 'Saving…' : mode === 'create' ? 'Create truck' : 'Save changes'}
          </button>
        )}
      </div>
    </div>
  )
}
