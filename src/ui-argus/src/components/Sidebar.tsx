import { NavLink, useNavigate } from 'react-router-dom'
import Icon, { type IconName } from './Icon'
import { CURRENT_USER } from '../data/fixtures'

/**
 * The nav rail from the mockups' `Sidebar` component. The mockup gated the two
 * nav groups behind a `role` prop (`sc-if`); until auth/session state exists
 * (INTEGRATION.md gaps #3/#4) there's no logged-in role to gate on, so both
 * groups are shown and the single fixture user (`CURRENT_USER`) fills the
 * footer. When auth lands: read role from session and hide the group the user
 * can't see, and drive the footer from the real user.
 */

interface NavItem {
  to: string
  label: string
  icon: IconName
  soon?: boolean
}

const MONITORING: NavItem[] = [
  { to: '/', label: 'Live operations', icon: 'eye' },
  { to: '/history', label: 'Trip history', icon: 'list', soon: true },
]

const OPERATIONS: NavItem[] = [
  { to: '/routes', label: 'Routes & trips', icon: 'route' },
]

const RESOURCES: NavItem[] = [
  { to: '/fleet', label: 'Fleet', icon: 'truck' },
  { to: '/drivers', label: 'Drivers', icon: 'users' },
  { to: '/access', label: 'Access', icon: 'lock', soon: true },
]

function Group({ title, items }: { title: string; items: NavItem[] }) {
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
      {items.map((item) => (
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
  const u = CURRENT_USER
  const initials = (u.first_name[0] + u.last_name[0]).toUpperCase()
  const roleLabel =
    u.role === 'guard' ? 'Control Tower' : 'Administration / Logistics'

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

        <Group title="Monitoring" items={MONITORING} />
        <Group title="Operations" items={OPERATIONS} />
        <Group title="Resources" items={RESOURCES} />
      </div>

      <button
        onClick={() => navigate('/login')}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '12px 8px 2px',
          background: 'transparent',
          border: 'none',
          borderTop: '1px solid var(--border-soft)',
          color: 'var(--text)',
          font: 'inherit',
          textAlign: 'left',
          cursor: 'pointer',
        }}
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
          <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>
            Sign out
          </span>
        </span>
        <Icon
          name="logout"
          size={16}
          style={{ marginLeft: 'auto', color: 'var(--text-faint)' }}
        />
      </button>
    </aside>
  )
}
