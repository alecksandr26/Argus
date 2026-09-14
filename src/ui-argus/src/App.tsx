import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import AppLayout from './components/AppLayout'
import ProtectedRoute from './components/ProtectedRoute'
import Login from './pages/Login'
import LiveOps from './pages/LiveOps'
import AlertTriage from './pages/AlertTriage'
import Fleet from './pages/Fleet'
import Drivers from './pages/Drivers'
import TravelManagement from './pages/TravelManagement'

/**
 * Route skeleton for the two MVP roles this app serves — Control Tower
 * (Guardian/Monitor) and Administration/Logistics (Root/Admin/Operator) —
 * see the "Argus — Mockups de UI" design canvas for what each screen looks
 * like and CLAUDE.md for why this specific screen set was prioritized.
 *
 * `ProtectedRoute` gates the whole `AppLayout` tree behind a real session
 * (INTEGRATION.md gap #4) — `Login` stays outside it. The screens themselves
 * still render fake data from `src/data/fixtures.ts` except for the session
 * user itself; wiring the rest of them to the real API is tracked per-screen
 * in INTEGRATION.md.
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
            <Route path="/alerts/:alertId" element={<AlertTriage />} />

            {/* Administration / Logistics */}
            <Route path="/fleet" element={<Fleet />} />
            <Route path="/drivers" element={<Drivers />} />
            <Route path="/routes" element={<TravelManagement />} />
          </Route>
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
