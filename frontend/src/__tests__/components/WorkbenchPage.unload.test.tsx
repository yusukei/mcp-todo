/**
 * Unload-time flush invariants (P12, P13, P14).
 *
 *   P12 — mutation triggers debounced server PUT
 *   P13 — visibilitychange (hidden) cancels debounce, calls beaconLayout
 *   P14 — pagehide cancels debounce, calls beaconLayout
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { useEffect, useRef } from 'react'
import { act, render, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import WorkbenchPage from '../../pages/WorkbenchPage'
import { server } from '../mocks/server'

const PROJECT_A = '69bfffad73ed736a9d13fd0f'

interface ProbeTree {
  kind: 'tabs' | 'split'
  id: string
  tabs?: Array<{ id: string; paneType: string }>
  children?: ProbeTree[]
}

interface CapturedCallbacks {
  onConfigChange?: (paneId: string, patch: Record<string, unknown>) => void
  tree?: ProbeTree
}

const renderHistory: CapturedCallbacks[] = []

vi.mock('../../workbench/WorkbenchLayout', () => {
  function ProbeLayout(
    props: CapturedCallbacks & { tree: ProbeTree; projectId: string },
  ) {
    const ref = useRef(0)
    useEffect(() => {
      ref.current += 1
    })
    renderHistory.push({
      onConfigChange: props.onConfigChange,
      tree: props.tree,
    })
    return <div data-testid="probe-layout" />
  }
  return { default: ProbeLayout, registerTabStrip: () => () => {} }
})

/** default layout の最初の pane id を tree から拾う. v2 reducer は
 *  存在しない paneId への configChange を no-op として扱い save を
 *  trigger しないため (echo loop 防止と同じ理屈)、テストは本物の
 *  paneId を使う必要がある. */
function firstPaneId(tree: ProbeTree): string {
  if (tree.kind === 'tabs') return tree.tabs![0].id
  return firstPaneId(tree.children![0])
}

let beacons: Array<{ url: string; data: BodyInit | null }> = []

beforeEach(() => {
  renderHistory.length = 0
  beacons = []
  window.sessionStorage.setItem('workbench:clientId', 'this-tab')
  // Stub navigator.sendBeacon
  Object.defineProperty(navigator, 'sendBeacon', {
    value: vi.fn((url: string, data?: BodyInit | null) => {
      beacons.push({ url, data: data ?? null })
      return true
    }),
    configurable: true,
  })
  server.use(
    http.get('/api/v1/projects/:projectId', () =>
      HttpResponse.json({
        id: 'pid',
        name: 'project',
        members: [],
        remote: null,
        status: 'active',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      }),
    ),
    http.get('/api/v1/workbench/layouts/:projectId', () =>
      HttpResponse.json({ detail: 'not found' }, { status: 404 }),
    ),
  )
})

afterEach(() => {
  // Restore default navigator.sendBeacon (jsdom has none, set undefined)
  Object.defineProperty(navigator, 'sendBeacon', {
    value: undefined,
    configurable: true,
  })
})

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/projects/${PROJECT_A}`]}>
        <Routes>
          <Route path="/projects/:projectId" element={<WorkbenchPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Workbench / Persistence — P12: mutation triggers debounced PUT', () => {
  it('a configChange mutation produces a PUT to /api/v1/workbench/layouts', async () => {
    const puts: Array<{ pid: string; body: unknown }> = []
    server.use(
      http.put('/api/v1/workbench/layouts/:projectId', async ({ request, params }) => {
        puts.push({ pid: params.projectId as string, body: await request.json() })
        return HttpResponse.json({ updated_at: 'ts' })
      }),
    )
    renderPage()
    await waitFor(() => {
      expect(renderHistory.length).toBeGreaterThan(0)
    })
    const initial = renderHistory[renderHistory.length - 1]
    const paneId = firstPaneId(initial.tree!)
    await act(async () => {
      initial.onConfigChange!(paneId, { _bumped: 1 })
    })
    // Wait past the 500 ms debounce.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 700))
    })
    expect(puts.length).toBeGreaterThan(0)
    expect(puts[0].pid).toBe(PROJECT_A)
  })
})

describe('Workbench / Persistence — P13: visibilitychange (hidden) flushes via beacon', () => {
  it('calls navigator.sendBeacon when visibilitychange fires with hidden state', async () => {
    renderPage()
    await waitFor(() => {
      expect(renderHistory.length).toBeGreaterThan(0)
    })
    const initial = renderHistory[renderHistory.length - 1]
    const paneId = firstPaneId(initial.tree!)
    // Mutate to dirty the state (so flushNow has something to write).
    await act(async () => {
      initial.onConfigChange!(paneId, { _bumped: 1 })
    })
    // Force visibilityState to 'hidden' and dispatch the event.
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'hidden',
    })
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(beacons.length).toBeGreaterThan(0)
    expect(beacons[0].url).toContain(`/workbench/layouts/${PROJECT_A}/beacon`)
  })
})

describe('Workbench / Persistence — P14: pagehide flushes via beacon', () => {
  it('calls navigator.sendBeacon when pagehide fires', async () => {
    renderPage()
    await waitFor(() => {
      expect(renderHistory.length).toBeGreaterThan(0)
    })
    const initial = renderHistory[renderHistory.length - 1]
    const paneId = firstPaneId(initial.tree!)
    await act(async () => {
      initial.onConfigChange!(paneId, { _bumped: 1 })
    })
    await act(async () => {
      window.dispatchEvent(new Event('pagehide'))
    })
    expect(beacons.length).toBeGreaterThan(0)
    expect(beacons[0].url).toContain(`/workbench/layouts/${PROJECT_A}/beacon`)
  })
})
