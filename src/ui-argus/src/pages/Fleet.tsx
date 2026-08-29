import { useMemo, useState } from 'react'
import PageHeader from '../components/PageHeader'
import SearchBox from '../components/SearchBox'
import RecordTable, { type Column } from '../components/RecordTable'
import StatusPill from '../components/StatusPill'
import Icon from '../components/Icon'
import { trucks as seedTrucks } from '../data/fixtures'
import { relativeTime } from '../utils/format'
import { truckStatus } from '../utils/status'
import type { Truck, TruckStatus } from '../types'

/**
 * Mockup: "Administration — Fleet". CRUD over the `Truck` entity, all in local
 * state — the list starts from the fixtures and "Save" mutates a `useState`
 * copy. Wiring target: `GET/POST/PUT/DELETE /api/trucks` (INTEGRATION.md).
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
  const [list, setList] = useState<Truck[]>(seedTrucks)
  const [query, setQuery] = useState('')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [mode, setMode] = useState<'edit' | 'create' | null>(null)

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

  function save() {
    if (!draft) return
    const now = new Date().toISOString()
    if (mode === 'create') {
      const id = `trk-${Math.random().toString(36).slice(2, 7)}`
      setList((prev) => [
        {
          ...draft,
          id_truck: id,
          raspberry_pi_mac: draft.raspberry_pi_mac || null,
          esp32_id: draft.esp32_id || null,
          created_at: now,
          updated_at: now,
        },
        ...prev,
      ])
    } else {
      setList((prev) =>
        prev.map((t) =>
          t.id_truck === draft.id_truck
            ? {
                ...t,
                ...draft,
                raspberry_pi_mac: draft.raspberry_pi_mac || null,
                esp32_id: draft.esp32_id || null,
                updated_at: now,
              }
            : t,
        ),
      )
    }
    closePanel()
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
              <button className="btn btn--accent" onClick={startCreate}>
                <Icon name="plus" size={15} strokeWidth={2} />
                Add truck
              </button>
            </>
          }
        />

        <RecordTable
          columns={columns}
          rows={filtered}
          getId={(t) => t.id_truck}
          selectedId={draft?.id_truck}
          onSelect={selectRow}
          emptyLabel="No truck matches the search."
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
          {mode === 'create' ? 'New truck' : 'Edit truck'}
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
          onChange={(e) => set({ plate_number: e.target.value })}
        />
      </label>
      <div style={{ display: 'flex', gap: 10 }}>
        <label className="field" style={{ flex: 1 }}>
          <span className="field__label">Make</span>
          <input
            className="input"
            value={draft.brand}
            onChange={(e) => set({ brand: e.target.value })}
          />
        </label>
        <label className="field" style={{ flex: 1 }}>
          <span className="field__label">Model</span>
          <input
            className="input"
            value={draft.model}
            onChange={(e) => set({ model: e.target.value })}
          />
        </label>
      </div>
      <label className="field">
        <span className="field__label">Unit no.</span>
        <input
          className="input mono"
          value={draft.company_number}
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
          onChange={(e) => set({ raspberry_pi_mac: e.target.value })}
        />
      </label>
      <label className="field">
        <span className="field__label">ESP32 ID</span>
        <input
          className="input mono"
          placeholder="ESP32-…"
          value={draft.esp32_id}
          onChange={(e) => set({ esp32_id: e.target.value })}
        />
      </label>

      <label className="field">
        <span className="field__label">Operative status</span>
        <select
          className="input"
          value={draft.operative_status}
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
          Cancel
        </button>
        <button className="btn btn--accent btn--block" onClick={onSave}>
          {mode === 'create' ? 'Create truck' : 'Save changes'}
        </button>
      </div>
    </div>
  )
}
