/**
 * WorkbenchPage primary-tabgroup invariants (post P3-5 simplification).
 *
 * 履歴:
 *   - P0-2 で breadcrumb (H1/H2) を撤去
 *   - P3-5 で Pane menu (⋮) と Copy URL ボタンを Tab strip から撤去
 *
 * Tab strip 右側に残るのは「+ (Add tab)」のみ。Layout reset modal は
 * hotkey (Cmd+Shift+R) 経由でも開くので、その経路だけテストする。
 *
 *   * H6 — Cmd+Shift+R で Reset confirm modal が開き、ESC でキャンセル、
 *          Enter で確定する
 */
import { describe, expect, it, vi, beforeAll, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
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
          <Route
            path="/projects"
            element={<div data-testid="projects-list">PROJECTS LIST</div>}
          />
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

async function waitForReady() {
  await waitFor(() => {
    expect(screen.queryAllByTestId('probe').length).toBeGreaterThan(0)
  })
}

// ── H6: Reset layout via Cmd+Shift+R hotkey (modal ESC / Enter) ──

describe('Workbench / Header — H6: Cmd+Shift+R で Reset modal が開く', () => {
  it('hotkey で modal を開き、ESC でキャンセル / Enter で確定', async () => {
    renderPage()
    await waitForReady()

    // Cmd+Shift+R で modal を発火
    fireEvent.keyDown(window, {
      key: 'R',
      code: 'KeyR',
      metaKey: true,
      shiftKey: true,
    })
    await waitFor(() => {
      expect(screen.queryByText(/Replace current layout\?/i)).not.toBeNull()
    })

    // ESC で閉じる
    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => {
      expect(screen.queryByText(/Replace current layout\?/i)).toBeNull()
    })

    // 再度開いて Enter で確定 (もう modal は閉じる)
    fireEvent.keyDown(window, {
      key: 'R',
      code: 'KeyR',
      metaKey: true,
      shiftKey: true,
    })
    await waitFor(() => {
      expect(screen.queryByText(/Replace current layout\?/i)).not.toBeNull()
    })
    fireEvent.keyDown(window, { key: 'Enter' })
    await waitFor(() => {
      expect(screen.queryByText(/Replace current layout\?/i)).toBeNull()
    })
  })
})
