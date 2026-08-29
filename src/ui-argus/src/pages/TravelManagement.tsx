import { useMemo, useState } from 'react'
import PageHeader from '../components/PageHeader'
import SearchBox from '../components/SearchBox'
import RecordTable, { type Column } from '../components/RecordTable'
import StatusPill from '../components/StatusPill'
import Icon from '../components/Icon'
import {
  drivers,
  routes as seedRoutes,
  trucks,
  driverById,
  truckById,
} from '../data/fixtures'
import { clock } from '../utils/format'
import { isActiveRoute, routeStatus } from '../utils/status'
import type { Route } from '../types'

/**
 * Mockup: "Administration — Routes & trips". The table lists `Route` rows; the
 * right panel creates a new one (status `scheduled`). Wiring target:
 * `GET/POST/PUT/DELETE /api/routes` — and the mockup's note that distance/ETA
 * "are computed with OSRM on confirm" means the create submit eventually calls
 * OSRM before persisting (INTEGRATION.md, TravelManagement row).
 */

type NewRoute = {
  id_driver: string
  id_truck: string
  origin_name: string
  destination_name: string
  date: string
  departure: string
}

const blankRoute = (): NewRoute => ({
  id_driver: drivers[0]?.id_driver ?? '',
  id_truck: trucks[0]?.id_truck ?? '',
  origin_name: 'CDMX',
  destination_name: 'Puebla',
  date: '2026-08-24',
  departure: '15:30',
})

const driverName = (id: string) => {
  const d = driverById(id)
  return d ? `${d.first_name} ${d.last_name}` : '—'
}

export default function TravelManagement() {
  const [list, setList] = useState<Route[]>(seedRoutes)
  const [query, setQuery] = useState('')
  const [form, setForm] = useState<NewRoute>(blankRoute)

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return list
    return list.filter((r) => {
      const truck = truckById(r.id_truck)
      return `${r.origin_name} ${r.destination_name} ${driverName(r.id_driver)} ${
        truck?.plate_number ?? ''
      }`
        .toLowerCase()
        .includes(q)
    })
  }, [list, query])

  const inProgress = list.filter((r) =>
    isActiveRoute(r.operative_status),
  ).length
  const scheduled = list.filter(
    (r) => r.operative_status === 'scheduled',
  ).length

  function createRoute() {
    const iso = (t: string) => `${form.date}T${t}:00-06:00`
    const now = new Date().toISOString()
    const route: Route = {
      id_route: `rt-${Math.random().toString(36).slice(2, 7)}`,
      id_driver: form.id_driver,
      id_truck: form.id_truck,
      origin_name: form.origin_name,
      destination_name: form.destination_name,
      // TODO(INTEGRATION.md): destination_coordinates + estimated_arrival come
      // back from OSRM on confirm; stubbed here.
      destination_coordinates: { lat: 0, lon: 0 },
      estimated_departure: iso(form.departure),
      estimated_arrival: null,
      actual_departure: null,
      actual_arrival: null,
      operative_status: 'scheduled',
      created_at: now,
      updated_at: now,
    }
    setList((prev) => [route, ...prev])
    setForm(blankRoute())
  }

  const columns: Column<Route>[] = [
    {
      header: 'Origin → Destination',
      cell: (r) => (
        <span style={{ fontWeight: 600 }}>
          {r.origin_name} → {r.destination_name}
        </span>
      ),
    },
    { header: 'Driver', cell: (r) => driverName(r.id_driver) },
    {
      header: 'Truck',
      cell: (r) => (
        <span className="mono">
          {truckById(r.id_truck)?.plate_number ?? '—'}
        </span>
      ),
    },
    {
      header: 'Est. departure',
      cell: (r) => (
        <span className="mono">{clock(r.estimated_departure)}</span>
      ),
    },
    {
      header: 'Status',
      cell: (r) => <StatusPill badge={routeStatus[r.operative_status]} />,
    },
    {
      header: 'ETA',
      cell: (r) => (
        <span className="mono">
          {clock(r.actual_arrival ?? r.estimated_arrival)}
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
          title="Routes & trips"
          subtitle={`${inProgress} trips in progress · ${scheduled} scheduled`}
          actions={
            <SearchBox
              value={query}
              onChange={setQuery}
              placeholder="Search route or truck…"
            />
          }
        />

        <RecordTable
          columns={columns}
          rows={filtered}
          getId={(r) => r.id_route}
          emptyLabel="No route matches the search."
        />
      </div>

      {/* new route panel */}
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
        <span style={{ fontSize: 14, fontWeight: 600 }}>New route</span>

        <label className="field">
          <span className="field__label">Driver</span>
          <select
            className="input"
            value={form.id_driver}
            onChange={(e) =>
              setForm({ ...form, id_driver: e.target.value })
            }
          >
            {drivers.map((d) => (
              <option key={d.id_driver} value={d.id_driver}>
                {d.first_name} {d.last_name}
                {d.operative_status === 'resting' ? ' — available' : ''}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field__label">Truck</span>
          <select
            className="input"
            value={form.id_truck}
            onChange={(e) =>
              setForm({ ...form, id_truck: e.target.value })
            }
          >
            {trucks.map((t) => (
              <option key={t.id_truck} value={t.id_truck}>
                {t.plate_number}
                {t.operative_status === 'maintenance' ? ' — in shop' : ''}
              </option>
            ))}
          </select>
        </label>

        <div style={{ height: 1, background: 'var(--border-soft)' }} />

        <label className="field">
          <span className="field__label">Origin</span>
          <input
            className="input"
            value={form.origin_name}
            onChange={(e) =>
              setForm({ ...form, origin_name: e.target.value })
            }
          />
        </label>
        <label className="field">
          <span className="field__label">Destination</span>
          <input
            className="input"
            value={form.destination_name}
            onChange={(e) =>
              setForm({ ...form, destination_name: e.target.value })
            }
          />
        </label>
        <div style={{ display: 'flex', gap: 10 }}>
          <label className="field" style={{ flex: 1 }}>
            <span className="field__label">Date</span>
            <input
              className="input mono"
              type="date"
              value={form.date}
              onChange={(e) => setForm({ ...form, date: e.target.value })}
            />
          </label>
          <label className="field" style={{ flex: 1 }}>
            <span className="field__label">Est. departure</span>
            <input
              className="input mono"
              type="time"
              value={form.departure}
              onChange={(e) =>
                setForm({ ...form, departure: e.target.value })
              }
            />
          </label>
        </div>

        <div
          style={{
            background: 'var(--surface-2)',
            border: '1px solid var(--border-soft)',
            borderRadius: 8,
            padding: '10px 12px',
            display: 'flex',
            gap: 8,
            alignItems: 'flex-start',
          }}
        >
          <Icon
            name="eye-brand"
            size={14}
            strokeWidth={1.6}
            style={{ color: 'var(--accent)', marginTop: 1 }}
          />
          <span
            style={{
              fontSize: 11,
              color: 'var(--text-soft)',
              lineHeight: 1.5,
            }}
          >
            Distance and estimated time are computed with OSRM when the route is
            confirmed.
          </span>
        </div>

        <div style={{ display: 'flex', gap: 10, marginTop: 'auto' }}>
          <button
            className="btn btn--block"
            onClick={() => setForm(blankRoute())}
          >
            Reset
          </button>
          <button
            className="btn btn--accent btn--block"
            onClick={createRoute}
          >
            Create route
          </button>
        </div>
      </div>
    </div>
  )
}
