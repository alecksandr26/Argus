import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import AppLayout from './components/AppLayout'
import ProtectedRoute from './components/ProtectedRoute'
import RequireRole from './components/RequireRole'
import Login from './pages/Login'
import LiveOps from './pages/LiveOps'
import RouteHistory from './pages/RouteHistory'
import AlertTriage from './pages/AlertTriage'
import Fleet from './pages/Fleet'
import Drivers from './pages/Drivers'
import TravelManagement from './pages/TravelManagement'
import Access from './pages/Access'
import Profile from './pages/Profile'

/**
 * Route skeleton covering all four roles — Control Tower (`guardian`, read-only + alert
 * review), Administration/Logistics (`root_admin` full control, `admin` scoped to trucks/
 * drivers/routes), and `truck_driver` — see the "Argus — Mockups de UI" design canvas for what
 * each screen looks like and CLAUDE.md for why this specific screen set was prioritized.
 *
 * `ProtectedRoute` gates the whole `AppLayout` tree behind a real session; `RequireRole` adds a
 * second, narrower gate on top for `/access` (root_admin/admin only — see `Sidebar.tsx` for the
 * matching nav-visibility rules and `Access.tsx`/`Fleet.tsx`/`Drivers.tsx`/`TravelManagement.tsx`
 * for per-role read vs. write UI). `Login` stays outside both gates.
 */
export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />

        <Route element={<ProtectedRoute />}>
          <Route element={<AppLayout />}>
            {/* Control Tower */}
            <Route path="/" element={<LiveOps />} />
            <Route path="/history" element={<RouteHistory />} />
            <Route path="/alerts/:alertId" element={<AlertTriage />} />

            {/* Administration / Logistics */}
            <Route path="/fleet" element={<Fleet />} />
            <Route path="/drivers" element={<Drivers />} />
            <Route path="/routes" element={<TravelManagement />} />

            {/* Any authenticated role */}
            <Route path="/profile" element={<Profile />} />

            {/* root_admin + admin only (admin scoped to guardian accounts, see Access.tsx) */}
            <Route element={<RequireRole roles={['root_admin', 'admin']} />}>
              <Route path="/access" element={<Access />} />
            </Route>
          </Route>
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
