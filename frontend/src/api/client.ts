import { useAuthStore } from '../store/auth'

const BASE_URL = '/api/v1'
const DEFAULT_TIMEOUT = 30000

// ── Types ──────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
interface ApiResponse<T = any> {
  data: T
  status: number
  headers: Headers
}

interface RequestConfig {
  timeout?: number
  responseType?: 'json' | 'blob'
  headers?: Record<string, string>
  signal?: AbortSignal
  params?: Record<string, string | number | boolean | undefined | null> | object
}

// ── Auth loop detection ────────────────────────────

const AUTH_LOOP_PATH_PREFIXES = ['/auth/refresh', '/auth/logout']

function isAuthLoopUrl(url: string): boolean {
  const path = url.replace(/^\/api\/v1/, '')
  return AUTH_LOOP_PATH_PREFIXES.some(
    (p) => path === p || path.startsWith(p + '/') || path.startsWith(p + '?'),
  )
}

// ── Cross-tab refresh coordination ─────────────────

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

// ── Token refresh ──────────────────────────────────

let refreshPromise: Promise<void> | null = null

async function performRefresh(): Promise<void> {
  refreshChannel?.postMessage({ kind: 'started' } satisfies RefreshSignal)
  try {
    const resp = await fetch(`${BASE_URL}/auth/refresh`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })
    if (!resp.ok) throw new Error(`Refresh failed: ${resp.status}`)
    refreshChannel?.postMessage({ kind: 'succeeded' } satisfies RefreshSignal)
  } catch (err) {
    refreshChannel?.postMessage({ kind: 'failed' } satisfies RefreshSignal)
    useAuthStore.getState().setUser(null)
    throw err
  }
}

// ── Core request function ──────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function request<T = any>(
  method: string,
  url: string,
  body?: unknown,
  config?: RequestConfig,
  _retried?: boolean,
): Promise<ApiResponse<T>> {
  // Build URL with query params
  let fullUrl = `${BASE_URL}${url}`
  if (config?.params) {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(config.params)) {
      if (v !== undefined && v !== null) qs.set(k, String(v))
    }
    const qsStr = qs.toString()
    if (qsStr) fullUrl += (fullUrl.includes('?') ? '&' : '?') + qsStr
  }

  const timeout = config?.timeout ?? DEFAULT_TIMEOUT
  const headers: Record<string, string> = { ...config?.headers }

  // Let the browser set Content-Type automatically for FormData (with boundary).
  // For everything else, set application/json.
  if (body !== undefined && body !== null && !(body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
  }

  // Combine caller-supplied signal with timeout signal
  const timeoutController = new AbortController()
  const timeoutId = setTimeout(() => timeoutController.abort(), timeout)

  const signal = config?.signal
    ? AbortSignal.any([config.signal, timeoutController.signal])
    : timeoutController.signal

  let resp: Response
  try {
    resp = await fetch(fullUrl, {
      method,
      credentials: 'include',
      headers,
      body:
        body === undefined || body === null
          ? undefined
          : body instanceof FormData
            ? body
            : JSON.stringify(body),
      signal,
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new Error(`Request timeout after ${timeout}ms: ${method} ${url}`)
    }
    throw err
  } finally {
    clearTimeout(timeoutId)
  }

  // ── 401 auto-refresh ──
  if (resp.status === 401 && !_retried && !isAuthLoopUrl(url)) {
    if (!refreshPromise) {
      refreshPromise = (async () => {
        if (crossTabRefreshInFlight) {
          const ok = await waitForCrossTabRefresh()
          if (ok) return
        }
        await performRefresh()
      })().finally(() => {
        refreshPromise = null
      })
    }

    try {
      await refreshPromise
    } catch {
      throw new ApiError(resp.status, { detail: 'Not authenticated' })
    }

    return request<T>(method, url, body, config, true)
  }

  // ── Parse response ──
  let data: T
  if (config?.responseType === 'blob') {
    data = (await resp.blob()) as T
  } else {
    const text = await resp.text()
    data = text ? JSON.parse(text) : (null as T)
  }

  if (!resp.ok) {
    throw new ApiError(resp.status, data)
  }

  return { data, status: resp.status, headers: resp.headers }
}

// ── Error class ────────────────────────────────────

export class ApiError extends Error {
  status: number
  data: unknown
  response: { status: number; data: unknown }
  isApiError = true

  constructor(status: number, data: unknown) {
    const detail = (data as { detail?: string })?.detail ?? `HTTP ${status}`
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.data = data
    // Compatibility: some existing code checks error.response.status
    this.response = { status, data }
  }
}

// ── Public API (same interface as before) ──────────

export const api = {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  get: <T = any>(url: string, config?: RequestConfig) =>
    request<T>('GET', url, undefined, config),

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  post: <T = any>(url: string, body?: unknown, config?: RequestConfig) =>
    request<T>('POST', url, body, config),

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  patch: <T = any>(url: string, body?: unknown, config?: RequestConfig) =>
    request<T>('PATCH', url, body, config),

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  put: <T = any>(url: string, body?: unknown, config?: RequestConfig) =>
    request<T>('PUT', url, body, config),

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  delete: <T = any>(url: string, config?: RequestConfig) =>
    request<T>('DELETE', url, undefined, config),
}
