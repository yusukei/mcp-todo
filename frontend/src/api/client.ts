import axios from 'axios'
import { useAuthStore } from '../store/auth'

/**
 * Axios instance for the REST API.
 *
 * Authentication is handled via HttpOnly cookies set by the backend
 * (`access_token` + `refresh_token`). The frontend never reads or stores
 * tokens directly — `withCredentials: true` lets the browser ship the
 * cookies on every request, and the response interceptor reissues them
 * via `/auth/refresh` when the access token expires.
 */
export const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
  withCredentials: true,
})

let refreshPromise: Promise<void> | null = null

// Endpoints that must NOT be intercepted by the auto-refresh handler.
// Matched as PATH PREFIXES (not substrings) so a future endpoint like
// /auth/logout-all is not picked up by accident.
//
// - /auth/refresh — refreshing in response to a refresh failure is the
//   textbook infinite loop.
// - /auth/logout — even if 401 comes back, the client must propagate it
//   instead of trying to refresh; otherwise /refresh ↔ /logout ricochets.
const AUTH_LOOP_PATH_PREFIXES = ['/auth/refresh', '/auth/logout']

function isAuthLoopUrl(url: string | undefined): boolean {
  if (!url) return false
  // Strip the api baseURL if present so prefixes work for either
  // "/auth/refresh" or "/api/v1/auth/refresh" forms.
  const path = url.replace(/^https?:\/\/[^/]+/, '').replace(/^\/api\/v1/, '')
  return AUTH_LOOP_PATH_PREFIXES.some(
    (p) => path === p || path.startsWith(p + '/') || path.startsWith(p + '?'),
  )
}

// ── Cross-tab refresh coordination ───────────────────────────────
//
// Two browser tabs share the same HttpOnly refresh_token cookie. When
// both tabs simultaneously detect a 401 they each fire /auth/refresh,
// but the JTI is single-use on the server side: the first request wins
// and rotates the cookie, the second request comes back 401 "already
// used" — and the loser tab dumps the user to /login even though the
// session is in fact alive.
//
// We coordinate via BroadcastChannel: a tab that is about to refresh
// announces "refreshing" and other tabs that observe a 401 in the
// meantime wait on a "refreshed" signal instead of hitting the server
// themselves. The waiter then retries its original request with the
// freshly rotated cookie.
//
// BroadcastChannel is unavailable in some test runners and old
// browsers — fall back to the per-tab refreshPromise coalesce in that
// case (the cross-tab race remains, but single-tab behaviour is
// preserved).
type RefreshSignal =
  | { kind: 'started' }
  | { kind: 'succeeded' }
  | { kind: 'failed' }

const refreshChannel: BroadcastChannel | null =
  typeof BroadcastChannel !== 'undefined'
    ? new BroadcastChannel('mcp-todo-auth-refresh')
    : null

let crossTabRefreshInFlight = false
let crossTabWaiters: Array<(ok: boolean) => void> = []

function resolveWaiters(ok: boolean): void {
  const waiters = crossTabWaiters
  crossTabWaiters = []
  for (const w of waiters) w(ok)
}

if (refreshChannel) {
  refreshChannel.onmessage = (ev: MessageEvent<RefreshSignal>) => {
    const msg = ev.data
    if (msg.kind === 'started') {
      crossTabRefreshInFlight = true
    } else if (msg.kind === 'succeeded') {
      crossTabRefreshInFlight = false
      resolveWaiters(true)
    } else if (msg.kind === 'failed') {
      crossTabRefreshInFlight = false
      resolveWaiters(false)
    }
  }
}

function waitForCrossTabRefresh(timeoutMs = 5000): Promise<boolean> {
  return new Promise((resolve) => {
    const id = setTimeout(() => {
      // Timed out waiting — fall through to refreshing ourselves.
      crossTabWaiters = crossTabWaiters.filter((w) => w !== handler)
      resolve(false)
    }, timeoutMs)
    const handler = (ok: boolean): void => {
      clearTimeout(id)
      resolve(ok)
    }
    crossTabWaiters.push(handler)
  })
}

async function performRefresh(): Promise<void> {
  refreshChannel?.postMessage({ kind: 'started' } satisfies RefreshSignal)
  try {
    await axios.post(
      '/api/v1/auth/refresh',
      {},
      { withCredentials: true },
    )
    refreshChannel?.postMessage({ kind: 'succeeded' } satisfies RefreshSignal)
  } catch (err) {
    refreshChannel?.postMessage({ kind: 'failed' } satisfies RefreshSignal)
    // Refresh failed → drop local user state but do NOT call
    // /auth/logout. The server cookies are already invalid (or we
    // wouldn't be here), and hitting /auth/logout would itself 401
    // and re-enter this very interceptor.
    useAuthStore.getState().setUser(null)
    throw err
  }
}

api.interceptors.response.use(
  (res) => res,
  async (error) => {
    const cfg = error.config
    if (
      error.response?.status === 401 &&
      cfg &&
      !cfg._retried &&
      !isAuthLoopUrl(cfg.url)
    ) {
      cfg._retried = true

      if (!refreshPromise) {
        refreshPromise = (async () => {
          // If another tab has already announced a refresh, wait for
          // its result instead of racing it. Single-use JTI on the
          // server would otherwise reject our concurrent request.
          if (crossTabRefreshInFlight) {
            const ok = await waitForCrossTabRefresh()
            if (ok) return
            // Either timeout or the other tab failed → fall through
            // and try ourselves. If the other tab really failed the
            // server will tell us 401 again and we'll surface it.
          }
          await performRefresh()
        })().finally(() => {
          refreshPromise = null
        })
      }

      try {
        await refreshPromise
      } catch {
        return Promise.reject(error)
      }

      // Cookie has been refreshed; retry the original request.
      return api.request(cfg)
    }
    return Promise.reject(error)
  },
)
