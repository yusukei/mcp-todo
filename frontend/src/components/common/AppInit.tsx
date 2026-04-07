import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { api } from '../../api/client'
import { useAuthStore } from '../../store/auth'

/**
 * Bootstraps authentication state on first mount.
 *
 * Authentication is cookie-based, so we can't tell from JS whether a
 * valid session exists. The bootstrap simply asks the server: a 200
 * from `/auth/me` populates the user, anything else (including 401)
 * leaves the user null. The store always ends up with
 * `isInitialized=true` so route guards stop blocking.
 *
 * Lives in its own file (extracted from App.tsx) so the boot sequence
 * can be unit-tested in isolation without dragging in the full router.
 */
export default function AppInit({ children }: { children: ReactNode }) {
  const setUser = useAuthStore((s) => s.setUser)
  const setInitialized = useAuthStore((s) => s.setInitialized)

  useEffect(() => {
    api.get('/auth/me')
      .then((r) => setUser(r.data))
      .catch(() => {
        // No valid session — leave user null. Route guards will
        // redirect to /login. The HttpOnly cookie (if any) will be
        // cleared next time the user explicitly logs in or out.
      })
      .finally(() => setInitialized(true))
  }, [setUser, setInitialized])

  return <>{children}</>
}
