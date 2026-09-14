import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

/**
 * Gates every in-app route in one place (INTEGRATION.md gap #4) — mounted once, above the whole
 * `AppLayout` route tree in `App.tsx`, so it catches any current or future screen, including one
 * typed directly into the address bar, not just the ones a nav link points at.
 *
 * The originally-requested location is carried as router state so `Login` can send the user
 * back to where they were headed (`useLocation().state?.from`) instead of always landing at `/`
 * — this also covers a session going stale *while the app is open*: `AuthContext` clears the
 * session on any `401`, which re-renders this component and redirects the same way a missing
 * session at initial load does.
 */
export default function ProtectedRoute() {
  const { session } = useAuth()
  const location = useLocation()

  if (!session) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }

  return <Outlet />
}
