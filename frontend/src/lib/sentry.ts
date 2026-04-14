/**
 * Sentry-compatible error tracker integration.
 *
 * The DSN is fetched at runtime from GET /api/v1/public-config so that
 * no build-time environment variable is required. The backend auto-
 * discovers the DSN from the first active ErrorProject in the database.
 *
 * Errors captured before initialisation completes are buffered and
 * flushed once the SDK is ready, so no events are lost during the
 * async boot window.
 */
import * as Sentry from '@sentry/browser'

let initialized = false

/** Errors captured before initSentry() completes are queued here. */
const preInitQueue: Array<[unknown, Record<string, unknown>?]> = []

export async function initSentry(): Promise<void> {
  // Guard against multiple invocations (HMR, React StrictMode double-mount).
  if (initialized) return

  // ── Fetch DSN from backend ────────────────────────────────
  let res: Response
  try {
    res = await fetch('/api/v1/public-config')
  } catch (err) {
    // Network failure at boot (offline, DNS, etc.) — log loudly so the
    // operator notices, but do not crash the app.
    console.error('[error-tracker] Network error fetching public-config — Sentry disabled:', err)
    return
  }

  if (!res.ok) {
    console.error(`[error-tracker] /api/v1/public-config returned HTTP ${res.status} — Sentry disabled`)
    return
  }

  let data: { sentry_dsn?: string | null }
  try {
    data = (await res.json()) as { sentry_dsn?: string | null }
  } catch (err) {
    console.error('[error-tracker] Failed to parse public-config response — Sentry disabled:', err)
    return
  }

  const dsn = data.sentry_dsn ?? null
  if (!dsn) {
    // No ErrorProject configured — not an error, just unconfigured.
    console.info('[error-tracker] No active ErrorProject found — Sentry disabled.')
    return
  }

  // ── Initialise SDK ────────────────────────────────────────
  Sentry.init({
    dsn,
    release: __BUILD_TIMESTAMP__,
    environment: import.meta.env.MODE,
    // No performance tracing — error capture only.
    tracesSampleRate: 0,
  })
  initialized = true

  // ── Flush buffered pre-init errors ────────────────────────
  for (const [err, ctx] of preInitQueue.splice(0)) {
    _send(err, ctx)
  }
}

function _send(err: unknown, context?: Record<string, unknown>): void {
  Sentry.withScope((scope) => {
    if (context) scope.setExtras(context)
    Sentry.captureException(err)
  })
}

export function captureException(err: unknown, context?: Record<string, unknown>): void {
  if (initialized) {
    _send(err, context)
  } else {
    // Buffer so the event is sent once initSentry() completes, and log
    // immediately so nothing is invisible during the boot window.
    preInitQueue.push([err, context])
    console.error('[error-tracker] captureException (buffered, Sentry initialising):', err, context)
  }
}
