import { Outlet } from 'react-router-dom'
import Sidebar from './Sidebar'

/**
 * Shell for every authenticated screen: the fixed nav rail + the routed page.
 * `App.tsx` mounts this as a layout route so `Login` (which has no sidebar)
 * stays outside it.
 */
export default function AppLayout() {
  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      <Sidebar />
      <main style={{ flex: 1, minWidth: 0, overflow: 'auto' }}>
        <Outlet />
      </main>
    </div>
  )
}
