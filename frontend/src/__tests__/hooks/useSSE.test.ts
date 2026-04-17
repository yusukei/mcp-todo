import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement } from 'react'
import { useSSE } from '../../hooks/useSSE'
import { useAuthStore } from '../../store/auth'
import { createMockUser } from '../mocks/factories'

// Mock the api client
vi.mock('../../api/client', () => ({
  api: {
    post: vi.fn().mockResolvedValue({ data: { ticket: 'mock-ticket-123' } }),
  },
}))

// EventSource のモッククラス
class MockEventSource {
  static instances: MockEventSource[] = []

  url: string
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  readyState = 0

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  close = vi.fn(() => {
    this.readyState = 2
  })

  // テストからイベントを発火するユーティリティ
  simulateMessage(data: string) {
    this.onmessage?.({ data } as MessageEvent)
  }

  simulateError() {
    this.onerror?.()
  }
}

// jsdom に EventSource が存在しないためグローバルに差し替え
const originalEventSource = global.EventSource

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: React.ReactNode }) =>
    createElement(QueryClientProvider, { client: qc }, children)
}

/** Wait for the async connect() to resolve and EventSource to be created */
async function waitForEventSource() {
  await waitFor(() => {
    expect(MockEventSource.instances.length).toBeGreaterThan(0)
  })
}

describe('useSSE', () => {
  beforeEach(() => {
    MockEventSource.instances = []
    // @ts-expect-error - グローバルモック差し替え
    global.EventSource = MockEventSource
    useAuthStore.setState({ user: null, isInitialized: false })
  })

  afterEach(() => {
    global.EventSource = originalEventSource
  })

  it('未ログイン (auth store user が null) なら EventSource を生成しない', async () => {
    renderHook(() => useSSE(), { wrapper: makeWrapper() })
    // Give time for potential async operations
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50))
    })
    expect(MockEventSource.instances).toHaveLength(0)
  })

  it('ログイン済みなら ticket を取得して EventSource を生成する', async () => {
    useAuthStore.setState({ user: createMockUser() })
    renderHook(() => useSSE(), { wrapper: makeWrapper() })
    await waitForEventSource()
    expect(MockEventSource.instances).toHaveLength(1)
    expect(MockEventSource.instances[0].url).toContain('ticket=mock-ticket-123')
  })

  it('コンポーネントのアンマウント時に es.close() が呼ばれる', async () => {
    useAuthStore.setState({ user: createMockUser() })
    const { unmount } = renderHook(() => useSSE(), { wrapper: makeWrapper() })
    await waitForEventSource()
    unmount()
    expect(MockEventSource.instances[0].close).toHaveBeenCalledOnce()
  })

  it('onerror ハンドラが es.close() を呼ぶ', async () => {
    useAuthStore.setState({ user: createMockUser() })
    renderHook(() => useSSE(), { wrapper: makeWrapper() })
    await waitForEventSource()
    MockEventSource.instances[0].simulateError()
    expect(MockEventSource.instances[0].close).toHaveBeenCalledOnce()
  })

  it('connected イベントは invalidate を発火しない', async () => {
    const qc = new QueryClient()
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')

    useAuthStore.setState({ user: createMockUser() })
    renderHook(() => useSSE(), {
      wrapper: ({ children }) =>
        createElement(QueryClientProvider, { client: qc }, children),
    })
    await waitForEventSource()

    MockEventSource.instances[0].simulateMessage(
      JSON.stringify({ type: 'connected' })
    )

    expect(invalidateSpy).not.toHaveBeenCalled()
  })

  it('task.created イベントで tasks クエリを invalidate する', async () => {
    const qc = new QueryClient()
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')

    useAuthStore.setState({ user: createMockUser() })
    renderHook(() => useSSE(), {
      wrapper: ({ children }) =>
        createElement(QueryClientProvider, { client: qc }, children),
    })
    await waitForEventSource()

    MockEventSource.instances[0].simulateMessage(
      JSON.stringify({
        type: 'task.created',
        project_id: 'proj-1',
        data: { id: 'task-1' },
      })
    )

    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['tasks', 'proj-1'] })
    )
  })

  it('comment.added イベントで project-summary クエリを invalidate する', async () => {
    const qc = new QueryClient()
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')

    useAuthStore.setState({ user: createMockUser() })
    renderHook(() => useSSE(), {
      wrapper: ({ children }) =>
        createElement(QueryClientProvider, { client: qc }, children),
    })
    await waitForEventSource()

    MockEventSource.instances[0].simulateMessage(
      JSON.stringify({
        type: 'comment.added',
        project_id: 'proj-1',
        data: {},
      })
    )

    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['project-summary', 'proj-1'] })
    )
  })

  it('tasks.batch_updated イベントで tasks クエリと個別タスクを invalidate する', async () => {
    const qc = new QueryClient()
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')

    useAuthStore.setState({ user: createMockUser() })
    renderHook(() => useSSE(), {
      wrapper: ({ children }) =>
        createElement(QueryClientProvider, { client: qc }, children),
    })
    await waitForEventSource()

    MockEventSource.instances[0].simulateMessage(
      JSON.stringify({
        type: 'tasks.batch_updated',
        project_id: 'proj-1',
        data: { count: 2, task_ids: ['task-1', 'task-2'] },
      })
    )

    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['tasks', 'proj-1'] })
    )
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['task', 'task-1'] })
    )
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['task', 'task-2'] })
    )
  })

  it('project.created イベントで projects クエリを invalidate する', async () => {
    const qc = new QueryClient()
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')

    useAuthStore.setState({ user: createMockUser() })
    renderHook(() => useSSE(), {
      wrapper: ({ children }) =>
        createElement(QueryClientProvider, { client: qc }, children),
    })
    await waitForEventSource()

    MockEventSource.instances[0].simulateMessage(
      JSON.stringify({
        type: 'project.created',
        project_id: 'proj-1',
        data: { id: 'proj-1', name: 'New Project' },
      })
    )

    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['projects'] })
    )
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['project', 'proj-1'] })
    )
  })

  it('project.deleted イベントで projects クエリを invalidate する', async () => {
    const qc = new QueryClient()
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')

    useAuthStore.setState({ user: createMockUser() })
    renderHook(() => useSSE(), {
      wrapper: ({ children }) =>
        createElement(QueryClientProvider, { client: qc }, children),
    })
    await waitForEventSource()

    MockEventSource.instances[0].simulateMessage(
      JSON.stringify({
        type: 'project.deleted',
        project_id: 'proj-1',
        data: { id: 'proj-1' },
      })
    )

    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['projects'] })
    )
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['admin-projects'] })
    )
  })

  it('comment.added イベントで task クエリも invalidate する', async () => {
    const qc = new QueryClient()
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')

    useAuthStore.setState({ user: createMockUser() })
    renderHook(() => useSSE(), {
      wrapper: ({ children }) =>
        createElement(QueryClientProvider, { client: qc }, children),
    })
    await waitForEventSource()

    MockEventSource.instances[0].simulateMessage(
      JSON.stringify({
        type: 'comment.added',
        project_id: 'proj-1',
        data: { task_id: 'task-1', comment: { id: 'c1' } },
      })
    )

    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['task', 'task-1'] })
    )
  })

  it('不正な JSON を受信してもクラッシュしない', async () => {
    useAuthStore.setState({ user: createMockUser() })
    renderHook(() => useSSE(), { wrapper: makeWrapper() })
    await waitForEventSource()

    expect(() => {
      MockEventSource.instances[0].simulateMessage('invalid json {{{}')
    }).not.toThrow()
  })

  it('イベントの server_time を localStorage に保存する (S2-3 reconcile)', async () => {
    useAuthStore.setState({ user: createMockUser() })
    localStorage.removeItem('sse.lastServerTime')
    renderHook(() => useSSE(), { wrapper: makeWrapper() })
    await waitForEventSource()

    const ts = '2026-04-17T14:00:00+00:00'
    MockEventSource.instances[0].simulateMessage(
      JSON.stringify({
        type: 'task.updated',
        project_id: 'proj-1',
        data: { id: 'task-1' },
        server_time: ts,
      })
    )

    expect(localStorage.getItem('sse.lastServerTime')).toBe(ts)
  })

  it('task.linked イベントで source/target 両方の task クエリを invalidate する', async () => {
    const qc = new QueryClient()
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')

    useAuthStore.setState({ user: createMockUser() })
    renderHook(() => useSSE(), {
      wrapper: ({ children }) =>
        createElement(QueryClientProvider, { client: qc }, children),
    })
    await waitForEventSource()

    MockEventSource.instances[0].simulateMessage(
      JSON.stringify({
        type: 'task.linked',
        project_id: 'proj-1',
        data: { source_id: 'task-A', target_id: 'task-B', relation: 'blocks' },
      })
    )

    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['task', 'task-A'] })
    )
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['task', 'task-B'] })
    )
  })

  it('task.unlinked イベントでも両端の task クエリを invalidate する', async () => {
    const qc = new QueryClient()
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')

    useAuthStore.setState({ user: createMockUser() })
    renderHook(() => useSSE(), {
      wrapper: ({ children }) =>
        createElement(QueryClientProvider, { client: qc }, children),
    })
    await waitForEventSource()

    MockEventSource.instances[0].simulateMessage(
      JSON.stringify({
        type: 'task.unlinked',
        project_id: 'proj-1',
        data: { source_id: 'src', target_id: 'tgt' },
      })
    )

    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['task', 'src'] })
    )
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['task', 'tgt'] })
    )
  })

  it('再接続時に lastServerTime があれば tasks/projects を invalidate する (reconcile)', async () => {
    const qc = new QueryClient()
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')

    // Pre-seed the cursor so the next connection triggers reconcile.
    localStorage.setItem('sse.lastServerTime', '2026-04-17T13:00:00+00:00')
    useAuthStore.setState({ user: createMockUser() })

    renderHook(() => useSSE(), {
      wrapper: ({ children }) =>
        createElement(QueryClientProvider, { client: qc }, children),
    })
    await waitForEventSource()

    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['tasks'] })
    )
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['projects'] })
    )
  })
})
