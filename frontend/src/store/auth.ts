import { create } from 'zustand'
import type { User } from '../types'

/**
 * Auth state. Tokens themselves live in HttpOnly cookies set by the
 * backend, so this store only tracks the resolved user object and a
 * one-shot `isInitialized` flag for route guards.
 *
 * `logout()` calls `/auth/logout` to clear the server-side cookies and
 * then drops the local user. Errors during the network call are
 * swallowed — local state is always reset so the UI can't end up
 * stuck in a half-logged-out state when the server is unreachable.
 */
interface AuthState {
  user: User | null
  isInitialized: boolean
  setUser: (user: User | null) => void
  setInitialized: (v: boolean) => void
  logout: () => Promise<void>
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  isInitialized: false,
  setUser: (user) => set({ user }),
  setInitialized: (isInitialized) => set({ isInitialized }),
  logout: async () => {
    try {
      // Lazy import to avoid a circular dependency with api/client.ts
      // (which imports useAuthStore for its 401 handler).
      const { api } = await import('../api/client')
      await api.post('/auth/logout')
    } catch {
      // Ignore — we still drop local state below.
    }
    set({ user: null })
  },
}))
