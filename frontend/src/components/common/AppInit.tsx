import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { api } from '../../api/client'
import { useAuthStore } from '../../store/auth'

/**
 * Bootstraps authentication state on first mount.
 *
 * - With access_token in localStorage: fetch /auth/me, set the user
 *   on success or clear stale tokens on failure
 * - Without a token: skip the request and immediately mark the auth
 *   store as initialized so route guards stop showing the loading state
 *
 * Lives in its own file (extracted from App.tsx) so the boot sequence
 * can be unit-tested in isolation without dragging in the full router.
 */
export default function AppInit({ children }: { children: ReactNode }) {
  const setUser = useAuthStore((s) => s.setUser)
  const setInitialized = useAuthStore((s) => s.setInitialized)

  useEffect(() => {
    const token = localStorage.getItem('access_token')
    if (token) {
      api.get('/auth/me')
        .then((r) => setUser(r.data))
        .catch(() => {
          localStorage.removeItem('access_token')
          localStorage.removeItem('refresh_token')
        })
        .finally(() => setInitialized(true))
    } else {
      setInitialized(true)
    }
  }, [setUser, setInitialized])

  return <>{children}</>
}
