/**
 * Top-level shell — owns the Editorial Split sidebar (Phase 2) and the
 * `<Outlet />` for the active route.
 *
 * The sidebar comes in three flavours:
 *   * **SidebarFull** (260 px) — desktop default and mobile slideover.
 *   * **SidebarRail** (56 px)  — desktop collapsed state; the user
 *     toggles between full / rail and the choice is persisted in
 *     ``localStorage('mcp-todo:sidebarCollapsed')``.
 *   * **Mobile slideover** — same SidebarFull, fixed-positioned with a
 *     backdrop. Visible only when the hamburger button is clicked.
 */
import { useEffect, useLayoutEffect, useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { Menu } from 'lucide-react'
import { useSSE } from '../../hooks/useSSE'
import LiveActivityPanel from './LiveActivityPanel'
import ErrorBoundary, { PageErrorFallback } from './ErrorBoundary'
import SidebarFull from './sidebar/SidebarFull'
import SidebarRail from './sidebar/SidebarRail'

const COLLAPSE_STORAGE_KEY = 'mcp-todo:sidebarCollapsed'

/** Read the persisted collapse state synchronously to avoid a flash of
 *  the wrong sidebar on hydrate. ``useLayoutEffect`` could do the same
 *  but ``useState`` initialisers are simpler and SSR-safe (we're CSR
 *  only, but the guard keeps prod-builds-with-SSR-checks happy). */
function readInitialCollapsed(): boolean {
  if (typeof window === 'undefined') return false
  try {
    return window.localStorage.getItem(COLLAPSE_STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

export default function Layout() {
  const location = useLocation()
  const [mobileOpen, setMobileOpen] = useState(false)
  const [collapsed, setCollapsed] = useState<boolean>(readInitialCollapsed)
  useSSE()

  // Persist desktop collapse state. ``useLayoutEffect`` so the storage
  // write happens synchronously during commit and a subsequent reload
  // sees the most recent choice.
  useLayoutEffect(() => {
    try {
      window.localStorage.setItem(COLLAPSE_STORAGE_KEY, collapsed ? '1' : '0')
    } catch {
      /* private browsing — non-fatal */
    }
  }, [collapsed])

  // Close the mobile drawer on route change so the user never lands on
  // a new page with the slideover still open.
  useEffect(() => {
    setMobileOpen(false)
  }, [location.pathname])

  const closeMobile = () => setMobileOpen(false)

  return (
    <div className="flex h-screen bg-gray-900 text-gray-50">
      {/* ── Desktop sidebar (md+) ─────────────────────────── */}
      <div className="hidden md:flex">
        {collapsed ? (
          <SidebarRail onExpand={() => setCollapsed(false)} />
        ) : (
          <SidebarFull onCollapse={() => setCollapsed(true)} />
        )}
      </div>

      {/* ── Mobile backdrop ──────────────────────────────── */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          onClick={closeMobile}
          aria-hidden
        />
      )}

      {/* ── Mobile drawer (always SidebarFull) ───────────── */}
      <div
        className={[
          'fixed inset-y-0 left-0 z-50 transform transition-transform duration-200 ease-in-out md:hidden',
          mobileOpen ? 'translate-x-0' : '-translate-x-full',
        ].join(' ')}
      >
        <SidebarFull onCloseMobile={closeMobile} />
      </div>

      {/* ── Main column ──────────────────────────────────── */}
      <main className="flex flex-1 flex-col overflow-hidden">
        {/* Mobile header with hamburger. Desktop hides this strip
            because the brand mark lives inside the sidebar. */}
        <div className="flex items-center gap-2 border-b border-gray-700/40 bg-gray-950 px-4 py-3 md:hidden">
          <button
            onClick={() => setMobileOpen(true)}
            className="rounded-md p-1 text-gray-100 hover:bg-gray-700/60"
            aria-label="メニューを開く"
            type="button"
          >
            <Menu className="h-5 w-5" />
          </button>
          <div className="flex items-center gap-2">
            <span
              aria-hidden
              className="inline-block h-2 w-2 rotate-45 rounded-[2px] bg-accent-500"
            />
            <span className="font-serif text-sm font-medium text-gray-50">
              MCP Todo
            </span>
          </div>
        </div>
        <ErrorBoundary key={location.pathname} fallback={<PageErrorFallback />}>
          <Outlet />
        </ErrorBoundary>
      </main>

      {/* Cross-project Live Activity floating panel (S2-8). */}
      <LiveActivityPanel />
    </div>
  )
}
