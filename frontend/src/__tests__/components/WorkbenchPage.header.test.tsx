/**
 * WorkbenchPage primary-tabgroup invariants (H3-H6, P0-2 update).
 *
 * P0-2 撤去前の H1 (breadcrumb projects link) / H2 (project name) は
 * 設計プロト指示で削除済み。プロジェクトコンテキストはサイドバー
 * (active project highlight + collapsed rail dot) で surfaced する。
 *
 *   * H3 — Layout presets live inside the ⋮ menu.
 *   * H4 — Copy URL is a small icon button (aria-label) at the right
 *          edge of the tab strip on the primary group only.
 *   * H5 — Reset layout is a MenuItem inside the same ⋮ menu and
 *          still triggers the "Replace current layout?" modal.
 *   * H6 — modal ESC / Enter behaviour is unchanged.
 */
import { describe, expect, it, vi, beforeAll, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import WorkbenchPage from '../../pages/WorkbenchPage'
import { server } from '../mocks/server'
import * as paneRegistry from '../../workbench/paneRegistry'

const PROJECT_ID = '69bfffad73ed736a9d13fd0f'

function ProbePane() {
  return <div data-testid="probe" />
}

function renderPage() {
  vi.spyOn(paneRegistry, 'getPaneComponent').mockImplementation(
    () => ProbePane as unknown as ReturnType<typeof paneRegistry.getPaneComponent>,
  )
  server.use(
    http.get(`/api/v1/projects/${PROJECT_ID}`, () =>
      HttpResponse.json({
        id: PROJECT_ID,
        name: 'My Test Project',
        members: [],
        remote: null,
        status: 'active',
        is_locked: false,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      }),
    ),
    http.get('/api/v1/workbench/layouts/:projectId', () =>
      HttpResponse.json({ detail: 'not found' }, { status: 404 }),
    ),
  )
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/projects/${PROJECT_ID}`]}>
        <Routes>
          <Route path="/projects/:projectId" element={<WorkbenchPage />} />
          <Route path="/projects" element={<div data-testid="projects-list">PROJECTS LIST</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as { ResizeObserver?: unknown }).ResizeObserver = vi
      .fn()
      .mockImplementation(() => ({
        observe: vi.fn(),
        disconnect: vi.fn(),
      }))
  }
})

beforeEach(() => {
  window.sessionStorage.setItem('workbench:clientId', 'test-tab')
})

// ── H1, H2 (breadcrumb) は P0-2 で撤去。設計プロト変更指示
//    (chat1.md「上部の Project · mcp-todo / Workbench / Layout / URL の
//    部分は削除してください」) を尊重し、関連テストは廃止。

// ── Helper: WorkbenchPage が project を取得し終わるまで待つ ──
async function waitForReady() {
  // ペインが mount されると ProbePane が複数描画される。最低 1 つ
  // 出現するのを project query 完了の代理シグナルにする。
  await waitFor(() => {
    expect(screen.queryAllByTestId('probe').length).toBeGreaterThan(0)
  })
}

// ── H3: Layout presets via the ⋮ menu ───────────────────────────

describe('Workbench / Header — H3: Layout preset menu lists 5 presets', () => {
  it('opens the ⋮ menu with the preset entries when clicked', async () => {
    const user = userEvent.setup()
    renderPage()
    await waitForReady()
    // Phase 3: presets live inside the primary TabGroup's ⋮ menu.
    const menuBtn = await screen.findByRole('button', { name: /pane menu/i })
    await user.click(menuBtn)
    const labels = [
      /Tasks only/i,
      /Tasks \+ Detail/i,
      /Tasks \+ Terminal/i,
      /Doc \+ Files/i,
    ]
    for (const re of labels) {
      expect(screen.queryByText(re)).not.toBeNull()
    }
  })
})

// ── H4: Copy URL icon ───────────────────────────────────────────

describe('Workbench / Header — H4: Copy URL button', () => {
  it('calls navigator.clipboard.writeText with the current URL', async () => {
    const user = userEvent.setup()
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    renderPage()
    await waitForReady()
    const copyBtn = await screen.findByRole('button', { name: /URL をコピー/i })
    await user.click(copyBtn)
    expect(writeText).toHaveBeenCalledTimes(1)
    expect(writeText.mock.calls[0][0]).toMatch(/^http/)
  })
})

// ── H5: Reset layout opens the confirm modal ────────────────────

describe('Workbench / Header — H5: Reset button opens confirm modal', () => {
  it('shows a "Replace current layout?" modal when clicked', async () => {
    const user = userEvent.setup()
    renderPage()
    await waitForReady()
    await user.click(await screen.findByRole('button', { name: /pane menu/i }))
    const resetItem = await screen.findByRole('button', {
      name: /Reset layout/i,
    })
    await user.click(resetItem)
    expect(screen.getByText(/Replace current layout\?/i)).toBeInTheDocument()
  })
})

// ── H6: confirm modal ESC / Enter ───────────────────────────────

describe('Workbench / Header — H6: confirm modal ESC / Enter', () => {
  it('closes on ESC, confirms on Enter', async () => {
    const user = userEvent.setup()
    renderPage()
    await waitForReady()
    const openModal = async () => {
      await user.click(await screen.findByRole('button', { name: /pane menu/i }))
      await user.click(
        await screen.findByRole('button', { name: /Reset layout/i }),
      )
    }
    await openModal()
    expect(screen.getByText(/Replace current layout\?/i)).toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByText(/Replace current layout\?/i)).toBeNull()
    // Re-open and confirm via Enter — should also dismiss the modal.
    await openModal()
    fireEvent.keyDown(window, { key: 'Enter' })
    expect(screen.queryByText(/Replace current layout\?/i)).toBeNull()
  })
})
