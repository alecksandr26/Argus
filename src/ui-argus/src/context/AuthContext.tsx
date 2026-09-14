import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { login as loginRequest } from '../api/auth'
import { setUnauthorizedHandler } from '../api/client'
import type { LoginUser } from '../types'

const STORAGE_KEY = 'argus.session'

interface Session {
  token: string
  user: LoginUser
}

interface AuthContextValue {
  session: Session | null
  login: (email: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

function readStoredSession(): Session | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as Session) : null
  } catch {
    // Corrupt/blocked storage (private window, cleared mid-read, etc.) — treat as logged out
    // rather than crashing the app.
    return null
  }
}

/**
 * The single source of truth for "who's logged in" (INTEGRATION.md gap #3). Session is held in
 * `localStorage` under one key, `{ token, user }`, matching the backend's `HTTPBearer` scheme —
 * `Authorization: Bearer <token>` on any future authenticated `apiFetch` call.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(() => readStoredSession())

  const logout = useCallback(() => {
    setSession(null)
    try {
      localStorage.removeItem(STORAGE_KEY)
    } catch {
      // Storage inaccessible — nothing more to clean up; in-memory state is already cleared.
    }
  }, [])

  // See src/api/client.ts's `setUnauthorizedHandler` doc comment: any future authenticated
  // request that comes back 401 (an expired/invalid JWT — no refresh flow) clears the session
  // here, which flips `ProtectedRoute`'s check on next render and bounces back to `/login`.
  useEffect(() => {
    setUnauthorizedHandler(logout)
    return () => setUnauthorizedHandler(null)
  }, [logout])

  const login = useCallback(async (email: string, password: string) => {
    const response = await loginRequest(email, password)
    const next: Session = { token: response.access_token, user: response.user }
    setSession(next)
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    } catch {
      // Storage inaccessible — the session still works for this page load via in-memory state,
      // it just won't survive a reload.
    }
  }, [])

  const value = useMemo(() => ({ session, login, logout }), [session, login, logout])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth() must be used within an <AuthProvider>')
  return ctx
}
