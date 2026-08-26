import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import Login from './pages/Login'
import LiveOps from './pages/LiveOps'
import AlertTriage from './pages/AlertTriage'
import Fleet from './pages/Fleet'
import Drivers from './pages/Drivers'
import TravelManagement from './pages/TravelManagement'

/**
 * Route skeleton for the two MVP roles this app serves — Torre de Control
 * (Guardian/Monitor) and Administración/Logística (Root/Admin/Operator) —
 * see the "Argus — Mockups de UI" design canvas for what each screen looks
 * like and CLAUDE.md for why this specific screen set was prioritized.
 *
 * There's no auth or role-based guarding wired up yet: every route is
 * reachable directly. Add that once /api/auth exists on the backend, rather
 * than faking a role check against nothing here.
 */
export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />

        {/* Torre de Control */}
        <Route path="/" element={<LiveOps />} />
        <Route path="/alertas/:alertId" element={<AlertTriage />} />

        {/* Administración / Logística */}
        <Route path="/flota" element={<Fleet />} />
        <Route path="/conductores" element={<Drivers />} />
        <Route path="/rutas" element={<TravelManagement />} />

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
