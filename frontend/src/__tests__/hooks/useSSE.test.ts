import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement } from 'react'
import { useSSE } from '../../hooks/useSSE'

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

describe('useSSE', () => {
  beforeEach(() => {
    MockEventSource.instances = []
    // @ts-expect-error - グローバルモック差し替え
    global.EventSource = MockEventSource
    localStorage.clear()
  })

  afterEach(() => {
    global.EventSource = originalEventSource
  })

  it('access_token がない場合 EventSource を生成しない', () => {
    renderHook(() => useSSE(), { wrapper: makeWrapper() })
    expect(MockEventSource.instances).toHaveLength(0)
  })

  it('access_token がある場合 EventSource を生成する', () => {
    localStorage.setItem('access_token', 'my-token')
    renderHook(() => useSSE(), { wrapper: makeWrapper() })
    expect(MockEventSource.instances).toHaveLength(1)
    expect(MockEventSource.instances[0].url).toContain('my-token')
  })

  it('コンポーネントのアンマウント時に es.close() が呼ばれる', () => {
    localStorage.setItem('access_token', 'my-token')
    const { unmount } = renderHook(() => useSSE(), { wrapper: makeWrapper() })
    unmount()
    expect(MockEventSource.instances[0].close).toHaveBeenCalledOnce()
  })

  it('onerror ハンドラが es.close() を呼ぶ', () => {
    localStorage.setItem('access_token', 'my-token')
    renderHook(() => useSSE(), { wrapper: makeWrapper() })
    MockEventSource.instances[0].simulateError()
    expect(MockEventSource.instances[0].close).toHaveBeenCalledOnce()
  })

  it('connected イベントは invalidate を発火しない', () => {
    const qc = new QueryClient()
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')

    localStorage.setItem('access_token', 'my-token')
    renderHook(() => useSSE(), {
      wrapper: ({ children }) =>
        createElement(QueryClientProvider, { client: qc }, children),
    })

    MockEventSource.instances[0].simulateMessage(
      JSON.stringify({ type: 'connected' })
    )

    expect(invalidateSpy).not.toHaveBeenCalled()
  })

  it('task.created イベントで tasks クエリを invalidate する', () => {
    const qc = new QueryClient()
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')

    localStorage.setItem('access_token', 'my-token')
    renderHook(() => useSSE(), {
      wrapper: ({ children }) =>
        createElement(QueryClientProvider, { client: qc }, children),
    })

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

  it('comment.added イベントで project-summary クエリを invalidate する', () => {
    const qc = new QueryClient()
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')

    localStorage.setItem('access_token', 'my-token')
    renderHook(() => useSSE(), {
      wrapper: ({ children }) =>
        createElement(QueryClientProvider, { client: qc }, children),
    })

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

  it('不正な JSON を受信してもクラッシュしない', () => {
    localStorage.setItem('access_token', 'my-token')
    const { result } = renderHook(() => useSSE(), { wrapper: makeWrapper() })

    expect(() => {
      MockEventSource.instances[0].simulateMessage('invalid json {{{}')
    }).not.toThrow()
  })
})
