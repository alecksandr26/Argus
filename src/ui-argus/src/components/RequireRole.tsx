import { Navigate, Outlet } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import type { Role } from '../types'

/**
 * A second, narrower gate on top of `ProtectedRoute` (which only checks "is there a session at
 * all"). Used where a route needs to additionally check *which* role — e.g. `/access` is
 * `root_admin`/`admin` only. Renders inline rather than sending an unauthorized role to `/login`
 * (they *are* logged in, just not allowed here) — bounces to `/`, same as an unknown route.
 */
export default function RequireRole({ roles }: { roles: Role[] }) {
  const { session } = useAuth()

  if (!session || !roles.includes(session.user.role)) {
    return <Navigate to="/" replace />
  }

  return <Outlet />
}
