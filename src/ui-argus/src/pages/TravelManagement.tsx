import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import PageHeader from '../components/PageHeader'
import SearchBox from '../components/SearchBox'
import RecordTable, { type Column } from '../components/RecordTable'
import StatusPill from '../components/StatusPill'
import Icon from '../components/Icon'
import RouteEndActions from '../components/RouteEndActions'
import AssignGuardianDialog from '../components/AssignGuardianDialog'
import GuardianOptions from '../components/GuardianOptions'
import { useAuth } from '../context/AuthContext'
import { createRoute as createRouteApi, listRoutes, updateRoute } from '../api/routes'
import { listDrivers } from '../api/drivers'
import { listTrucks } from '../api/trucks'
import { listUsers } from '../api/users'
import { ApiError } from '../api/client'
import { clock } from '../utils/format'
import { isActiveRoute, routeStatusBadge } from '../utils/status'
import {
  guardianName,
  hasValidGuardianPhone,
  isOpenRoute,
  selectableGuardians,
} from '../utils/guardian'
import type { Driver, Route, Truck, User } from '../types'

/**
 * "Administration — Routes & trips" (the mockup's original label) against the real
 * `GET/POST /api/routes`. The deployed page titles this "Routes" instead, since the entity it
 * manages is `Route` alone. The mockup's note that distance/ETA "are computed with OSRM on
 * confirm" is still deferred (INTEGRATION.md) — `destination_coordinates`/`estimated_arrival`
 * stay stubbed on create, unchanged from before. The create panel is `root_admin`/`admin` only;
 * a `guardian` sees the route table read-only.
 *
 * Start trip moves a route to `in_progress`. Complete trip (`completed`) and Stop route
 * (`POST /api/routes/{id}/stop`, a required reason, the route becomes `cancelled` / "Stopped") go
 * through `RouteEndActions`' confirmation dialogs; both are root_admin/admin only and hidden once
 * a route has ended. Open incidents stay open either way. A route is only
 * "active" (what the truck's ESP32 gets from `GET /api/devices/me/active-route`, and so what its
 * alerts are filed under) while `in_progress`; nothing on the backend changes that by itself.
 * Starting is refused while the same truck already has an active trip: the device follows one
 * route, the newest by departure, so a second one would silently take its alerts.
 *
 * Guardians (route guardian contracts G1/G7): every new route needs one, picked from the active
 * guardian-role users (`GET /api/users`, root_admin/admin only; one without an E.164 phone is a
 * disabled option). The Guardian column reads the route's embedded `guardian`, so a guardian
 * viewer sees it too. Open routes (scheduled/in progress) can be given or changed a guardian
 * ("Assign guardian" / "Change guardian"), and a banner warns while any open route has none:
 * routes created before guardians existed keep working, but their critical-alert SMS go only to
 * the truck's fallback phone.
 */

type NewRoute = {
  id_driver: string
  id_truck: string
  origin_name: string
  destination_name: string
  date: string
  departure: string
  /** `''` until one is picked: required (G1). */
  id_guardian: string
}

const blankRoute = (drivers: Driver[], trucks: Truck[]): NewRoute => ({
  id_driver: drivers[0]?.id_driver ?? '',
  id_truck: trucks[0]?.id_truck ?? '',
  origin_name: 'CDMX',
  destination_name: 'Puebla',
  date: '2026-08-24',
  departure: '15:30',
  id_guardian: '',
})

export default function TravelManagement() {
  const { session } = useAuth()
  const token = session!.token
  const canWrite = session!.user.role === 'root_admin' || session!.user.role === 'admin'

  const [list, setList] = useState<Route[]>([])
  const [drivers, setDrivers] = useState<Driver[]>([])
  const [trucks, setTrucks] = useState<Truck[]>([])
  /** Active guardian-role users (`selectableGuardians`); stays empty for a guardian viewer. */
  const [guardians, setGuardians] = useState<User[]>([])
  const [guardianLoadError, setGuardianLoadError] = useState<string | null>(null)
  const [guardianError, setGuardianError] = useState<string | null>(null)
  const [assigning, setAssigning] = useState<Route | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [form, setForm] = useState<NewRoute>(blankRoute([], []))
  const [creating, setCreating] = useState(false)
  const [busyRoute, setBusyRoute] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    // Only root_admin/admin may list users (a guardian gets 403); the Guardian column reads the
    // embedded `route.guardian` instead. A failed guardian list doesn't hide the routes.
    const guardiansReq: Promise<User[] | Error> = canWrite
      ? listUsers(token).catch((err: unknown) =>
          err instanceof Error ? err : new Error('Request failed'),
        )
      : Promise.resolve([])
    Promise.all([listRoutes(token), listDrivers(token), listTrucks(token), guardiansReq])
      .then(([routes, driversList, trucksList, usersOrError]) => {
        if (cancelled) return
        setList(routes)
        setDrivers(driversList)
        setTrucks(trucksList)
        if (usersOrError instanceof Error) {
          setGuardianLoadError(`Couldn't load guardians: ${usersOrError.message}`)
        } else {
          setGuardianLoadError(null)
          setGuardians(selectableGuardians(usersOrError))
        }
        setForm(blankRoute(driversList, trucksList))
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : 'Failed to load routes')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [token, canWrite])

  const driverById = useMemo(
    () => new Map(drivers.map((d) => [d.id_driver, d])),
    [drivers],
  )
  const truckById = useMemo(() => new Map(trucks.map((t) => [t.id_truck, t])), [trucks])
  const driverName = (id: string) => {
    const d = driverById.get(id)
    return d ? `${d.first_name} ${d.last_name}` : '—'
  }

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return list
    return list.filter((r) => {
      const truck = truckById.get(r.id_truck)
      const driver = driverById.get(r.id_driver)
      const driver_name = driver ? `${driver.first_name} ${driver.last_name}` : '—'
      return `${r.origin_name} ${r.destination_name} ${driver_name} ${
        truck?.plate_number ?? ''
      }`
        .toLowerCase()
        .includes(q)
    })
  }, [list, query, truckById, driverById])

  const inProgress = list.filter((r) =>
    isActiveRoute(r.operative_status),
  ).length
  const scheduled = list.filter(
    (r) => r.operative_status === 'scheduled',
  ).length

  /** Open (scheduled/in progress) routes with no guardian: the banner's count. */
  const unguarded = list.filter((r) => isOpenRoute(r) && !r.guardian).length

  async function createRoute() {
    if (!form.id_guardian) {
      setGuardianError('Select a guardian for this route')
      return
    }
    setGuardianError(null)
    setCreating(true)
    setError(null)
    try {
      const created = await createRouteApi(
        {
          id_driver: form.id_driver,
          id_truck: form.id_truck,
          origin_name: form.origin_name,
          destination_name: form.destination_name,
          // TODO(INTEGRATION.md): destination_coordinates + estimated_arrival come back from
          // OSRM on confirm; stubbed here, unchanged from the fixture-era behavior.
          destination_coordinates: { lat: 0, lon: 0 },
          estimated_departure: `${form.date}T${form.departure}:00-06:00`,
          estimated_arrival: null,
          operative_status: 'scheduled',
          id_guardian: form.id_guardian,
        },
        token,
      )
      setList((prev) => [created, ...prev])
      setForm(blankRoute(drivers, trucks))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to create route')
    } finally {
      setCreating(false)
    }
  }

  const replaceRoute = (updated: Route) =>
    setList((prev) => prev.map((o) => (o.id_route === updated.id_route ? updated : o)))

  async function startTrip(r: Route) {
    setError(null)
    const running = list.find(
      (o) =>
        o.id_truck === r.id_truck && o.id_route !== r.id_route && isActiveRoute(o.operative_status),
    )
    if (running) {
      const plate = truckById.get(r.id_truck)?.plate_number ?? 'This truck'
      setError(
        `${plate} already has a trip in progress (${running.origin_name} → ${running.destination_name}). Complete it first.`,
      )
      return
    }
    setBusyRoute(r.id_route)
    try {
      const updated = await updateRoute(
        r.id_route,
        { operative_status: 'in_progress', actual_departure: new Date().toISOString() },
        token,
      )
      replaceRoute(updated)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to update the trip')
    } finally {
      setBusyRoute(null)
    }
  }

  const tripColumn: Column<Route> = {
    header: 'Trip',
    cell: (r) => {
      if (r.operative_status === 'completed' || r.operative_status === 'cancelled') {
        return <span style={{ color: 'var(--text-faint)', fontSize: 11.5 }}>—</span>
      }
      return (
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {r.operative_status === 'scheduled' && (
            <button
              type="button"
              className="btn btn--ghost"
              style={{ padding: '4px 10px', fontSize: 11.5 }}
              disabled={busyRoute !== null}
              onClick={(e) => {
                e.stopPropagation()
                void startTrip(r)
              }}
            >
              Start trip
            </button>
          )}
          <RouteEndActions
            route={r}
            role={session!.user.role}
            token={token}
            onChanged={replaceRoute}
            compact
            disabled={busyRoute !== null}
          />
          <button
            type="button"
            className="btn btn--ghost"
            style={{ padding: '4px 10px', fontSize: 11.5 }}
            disabled={busyRoute !== null}
            onClick={(e) => {
              e.stopPropagation()
              setAssigning(r)
            }}
          >
            {r.guardian ? 'Change guardian' : 'Assign guardian'}
          </button>
          {/* G7: RouteDeviceFileButton slot — in scope: `r` (this Route), its truck via
              `truckById.get(r.id_truck)` (Truck | undefined), `token`, and `setTrucks` for an
              `onTruckChange`. */}
        </div>
      )
    },
  }

  const guardianColumn: Column<Route> = {
    header: 'Guardian',
    cell: (r) => {
      if (r.guardian) {
        return (
          <span style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
            <span>{guardianName(r.guardian)}</span>
            <span className="mono" style={{ fontSize: 11, color: 'var(--text-faint)' }}>
              {r.guardian.phone_number}
            </span>
          </span>
        )
      }
      return (
        <span
          className={isOpenRoute(r) ? 'pill pill--warn' : 'pill'}
          style={{ fontSize: 10.5, padding: '3px 9px' }}
          title={
            r.id_guardian
              ? 'The assigned guardian no longer exists'
              : "Critical-alert SMS go only to the truck's fallback phone"
          }
        >
          No guardian
        </span>
      )
    },
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
    guardianColumn,
    {
      header: 'Truck',
      cell: (r) => (
        <span className="mono">
          {truckById.get(r.id_truck)?.plate_number ?? '—'}
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
      cell: (r) => (
        <span title={r.stop_reason ? `Stopped: ${r.stop_reason}` : undefined}>
          <StatusPill badge={routeStatusBadge(r)} />
        </span>
      ),
    },
    {
      header: 'ETA',
      cell: (r) => (
        <span className="mono">
          {clock(r.actual_arrival ?? r.estimated_arrival)}
        </span>
      ),
    },
    {
      header: 'Live',
      cell: (r) =>
        r.operative_status === 'scheduled' ? (
          <span style={{ color: 'var(--text-faint)', fontSize: 11.5 }}>—</span>
        ) : (
          <Link
            to={`/routes/${r.id_route}/live`}
            className="btn btn--ghost"
            style={{ padding: '4px 10px', fontSize: 11.5 }}
          >
            <Icon name="eye" size={13} /> {isActiveRoute(r.operative_status) ? 'View live' : 'View route'}
          </Link>
        ),
    },
    ...(canWrite ? [tripColumn] : []),
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
          title="Routes"
          subtitle={`${inProgress} trips in progress · ${scheduled} scheduled`}
          actions={
            <SearchBox
              value={query}
              onChange={setQuery}
              placeholder="Search route or truck…"
            />
          }
        />

        {error && (
          <div role="alert" style={{ color: 'var(--danger, #e5484d)', fontSize: 13 }}>
            {error}
          </div>
        )}

        {unguarded > 0 && (
          <div
            data-testid="no-guardian-banner"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '9px 12px',
              borderRadius: 8,
              fontSize: 12.5,
              color: 'var(--warn)',
              background: 'var(--warn-soft)',
              border: '1px solid var(--warn)',
            }}
          >
            <Icon name="alert-triangle" size={14} />
            <span>
              {unguarded} active route{unguarded === 1 ? ' has' : 's have'} no guardian
              {canWrite ? ' — assign one so critical alerts are texted' : ''}
            </span>
          </div>
        )}

        <RecordTable
          columns={columns}
          rows={filtered}
          getId={(r) => r.id_route}
          emptyLabel={loading ? 'Loading routes…' : 'No route matches the search.'}
        />
      </div>

      {/* new route panel — root_admin/admin only; a guardian sees the table only */}
      {canWrite && (
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

          <label className="field">
            <span className="field__label">Guardian</span>
            <select
              className="input"
              value={form.id_guardian}
              required
              aria-invalid={guardianError ? true : undefined}
              aria-describedby="route-guardian-help"
              onChange={(e) => {
                setForm({ ...form, id_guardian: e.target.value })
                setGuardianError(null)
              }}
            >
              <GuardianOptions guardians={guardians} />
            </select>
          </label>
          {guardianError || guardianLoadError ? (
            <div id="route-guardian-help" className="field__error" style={{ marginTop: -10 }}>
              {guardianError ?? guardianLoadError}
            </div>
          ) : (
            <span
              id="route-guardian-help"
              style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: -10 }}
            >
              {guardians.length === 0
                ? 'No active guardian yet. Add one in Access first.'
                : guardians.some(hasValidGuardianPhone)
                  ? 'Watches this route; the truck texts their phone on every critical alert.'
                  : 'No guardian has a valid E.164 phone yet. Fix one in Access first.'}
            </span>
          )}

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
              onClick={() => {
                setForm(blankRoute(drivers, trucks))
                setGuardianError(null)
              }}
            >
              Reset
            </button>
            <button
              className="btn btn--accent btn--block"
              onClick={createRoute}
              disabled={creating}
            >
              {creating ? 'Creating…' : 'Create route'}
            </button>
          </div>
        </div>
      )}

      {assigning && (
        <AssignGuardianDialog
          route={assigning}
          guardians={guardians}
          token={token}
          onCancel={() => setAssigning(null)}
          onDone={(updated) => {
            setAssigning(null)
            replaceRoute(updated)
          }}
        />
      )}
    </div>
  )
}
