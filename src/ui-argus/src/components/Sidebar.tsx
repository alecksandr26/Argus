import { NavLink, useNavigate } from 'react-router-dom'
import Icon, { type IconName } from './Icon'
import { useAuth } from '../context/AuthContext'
import type { Role } from '../types'

/**
 * The nav rail from the mockups' `Sidebar` component. The footer reads the real logged-in user
 * from `AuthContext`. Each `NavItem` now carries an optional `roles` allow-list, filtered
 * per-group before render: `Fleet`/`Drivers`/`Routes & trips` are visible to `root_admin`/
 * `admin`/`guardian` (guardian's view is read-only — enforced inside those pages, not by
 * hiding the nav item) but not `truck_driver`; `Access` is `root_admin`/`admin` only (an
 * `admin` session only ever manages guardian accounts there, see `Access.tsx`). Items with no
 * `roles` are visible to everyone.
 */

interface NavItem {
  to: string
  label: string
  icon: IconName
  soon?: boolean
  roles?: Role[]
}

const MONITORING: NavItem[] = [
  { to: '/', label: 'Live operations', icon: 'eye' },
  { to: '/history', label: 'Trip history', icon: 'list', soon: true },
]

const OPERATIONS: NavItem[] = [
  { to: '/routes', label: 'Routes & trips', icon: 'route', roles: ['root_admin', 'admin', 'guardian'] },
]

const RESOURCES: NavItem[] = [
  { to: '/fleet', label: 'Fleet', icon: 'truck', roles: ['root_admin', 'admin', 'guardian'] },
  { to: '/drivers', label: 'Drivers', icon: 'users', roles: ['root_admin', 'admin', 'guardian'] },
  { to: '/access', label: 'Access', icon: 'lock', roles: ['root_admin', 'admin'] },
]

function Group({ title, items, role }: { title: string; items: NavItem[]; role: Role }) {
  const visibleItems = items.filter((item) => !item.roles || item.roles.includes(role))
  if (visibleItems.length === 0) return null

  return (
    <nav style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <span
        style={{
          fontSize: 10.5,
          color: 'var(--text-faint)',
          textTransform: 'uppercase',
          letterSpacing: '0.06em',
          padding: '0 12px',
          marginBottom: 4,
        }}
      >
        {title}
      </span>
      {visibleItems.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.to === '/'}
          onClick={(e) => item.soon && e.preventDefault()}
          style={({ isActive }) => ({
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: '9px 12px',
            borderRadius: 8,
            fontSize: 13.5,
            fontWeight: 500,
            transition: 'background .15s',
            cursor: item.soon ? 'default' : 'pointer',
            background: isActive ? 'var(--accent-soft)' : 'transparent',
            color: isActive
              ? 'var(--accent)'
              : item.soon
                ? 'var(--text-faint)'
                : 'var(--text-soft)',
          })}
        >
          <Icon name={item.icon} />
          {item.label}
          {item.soon && (
            <span
              style={{
                marginLeft: 'auto',
                fontSize: 9.5,
                color: 'var(--text-faint)',
                border: '1px solid var(--border)',
                padding: '1px 6px',
                borderRadius: 20,
              }}
            >
              soon
            </span>
          )}
        </NavLink>
      ))}
    </nav>
  )
}

export default function Sidebar() {
  const navigate = useNavigate()
  const { session, logout } = useAuth()
  // `Sidebar` only ever renders inside `AppLayout`, which `ProtectedRoute` already guarantees
  // has a session — this fallback just satisfies TypeScript's null-narrowing, not a real state.
  const u = session?.user ?? { first_name: '?', last_name: '?', role: 'guardian' as const }
  const initials = (u.first_name[0] + u.last_name[0]).toUpperCase()
  const roleLabel =
    u.role === 'guardian'
      ? 'Control Tower'
      : u.role === 'root_admin'
        ? 'Administration / Logistics'
        : u.role === 'admin'
          ? 'Fleet Operations'
          : 'Truck Driver'

  return (
    <aside
      style={{
        width: 240,
        flexShrink: 0,
        background: 'var(--surface)',
        borderRight: '1px solid var(--border-soft)',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'space-between',
        padding: '22px 14px',
      }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 26 }}>
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 4,
            padding: '0 8px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Icon
              name="eye-brand"
              size={22}
              strokeWidth={1.8}
              style={{ color: 'var(--accent)' }}
            />
            <span
              style={{
                fontFamily: 'var(--font-display)',
                fontWeight: 700,
                fontSize: 18,
                letterSpacing: '0.02em',
              }}
            >
              ARGUS
            </span>
          </div>
          <span
            style={{
              fontSize: 10.5,
              color: 'var(--text-faint)',
              paddingLeft: 30,
              letterSpacing: '0.03em',
              textTransform: 'uppercase',
            }}
          >
            {roleLabel}
          </span>
        </div>

        <Group title="Monitoring" items={MONITORING} role={u.role} />
        <Group title="Operations" items={OPERATIONS} role={u.role} />
        <Group title="Resources" items={RESOURCES} role={u.role} />
      </div>

      <div style={{ borderTop: '1px solid var(--border-soft)', paddingTop: 6 }}>
        <NavLink
          to="/profile"
          style={({ isActive }) => ({
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: '9px 8px',
            borderRadius: 8,
            color: 'var(--text)',
            textDecoration: 'none',
            background: isActive ? 'var(--accent-soft)' : 'transparent',
          })}
        >
          <span
            style={{
              width: 32,
              height: 32,
              borderRadius: '50%',
              background: 'var(--surface-2)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 12,
              fontWeight: 600,
              color: 'var(--accent)',
              flexShrink: 0,
            }}
          >
            {initials}
          </span>
          <span
            style={{
              display: 'flex',
              flexDirection: 'column',
              lineHeight: 1.25,
              overflow: 'hidden',
            }}
          >
            <span
              style={{
                fontSize: 13,
                fontWeight: 500,
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {u.first_name} {u.last_name}
            </span>
            <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>My profile</span>
          </span>
          <Icon
            name="settings"
            size={15}
            style={{ marginLeft: 'auto', color: 'var(--text-faint)' }}
          />
        </NavLink>
        <button
          onClick={() => {
            logout()
            navigate('/login')
          }}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            width: '100%',
            padding: '8px 8px',
            background: 'transparent',
            border: 'none',
            color: 'var(--text-faint)',
            font: 'inherit',
            fontSize: 12.5,
            textAlign: 'left',
            cursor: 'pointer',
          }}
        >
          <Icon name="logout" size={15} />
          Sign out
        </button>
      </div>
    </aside>
  )
}
