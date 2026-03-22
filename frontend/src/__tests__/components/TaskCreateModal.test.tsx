import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import TaskCreateModal from '../../components/task/TaskCreateModal'
import { server } from '../mocks/server'
import { mockTask } from '../mocks/handlers'

function renderModal(onClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return {
    onClose,
    ...render(
      <QueryClientProvider client={qc}>
        <TaskCreateModal projectId="project-id-1" onClose={onClose} />
      </QueryClientProvider>
    ),
  }
}

describe('TaskCreateModal', () => {
  it('タイトル入力フィールドとボタンが描画される', () => {
    renderModal()
    expect(screen.getByPlaceholderText('タスクのタイトル')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '作成' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'キャンセル' })).toBeInTheDocument()
  })

  it('title が空の場合に送信ボタンが disabled', () => {
    renderModal()
    expect(screen.getByRole('button', { name: '作成' })).toBeDisabled()
  })

  it('title 入力後に送信ボタンが有効になる', async () => {
    renderModal()
    await userEvent.type(screen.getByPlaceholderText('タスクのタイトル'), 'New Task')
    expect(screen.getByRole('button', { name: '作成' })).not.toBeDisabled()
  })

  it('キャンセルボタンで onClose が呼ばれる', async () => {
    const { onClose } = renderModal()
    await userEvent.click(screen.getByRole('button', { name: 'キャンセル' }))
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('フォーム送信成功後に onClose が呼ばれる', async () => {
    const { onClose } = renderModal()
    await userEvent.type(screen.getByPlaceholderText('タスクのタイトル'), 'My New Task')
    await userEvent.click(screen.getByRole('button', { name: '作成' }))

    await waitFor(() => {
      expect(onClose).toHaveBeenCalledOnce()
    })
  })

  it('title が空のまま送信してもAPIリクエストを送らない', async () => {
    let called = false
    server.use(
      http.post('/api/v1/projects/:projectId/tasks', () => {
        called = true
        return HttpResponse.json(mockTask, { status: 201 })
      })
    )

    renderModal()
    // title が空なのでボタンは disabled、クリックしても何も起きない
    const submitBtn = screen.getByRole('button', { name: '作成' })
    expect(submitBtn).toBeDisabled()
    expect(called).toBe(false)
  })

  it('モーダル背景クリックで onClose が呼ばれる', async () => {
    const { onClose } = renderModal()
    // 背景 (fixed inset-0 の div) をクリック
    const backdrop = screen.getByText('タスクを作成').closest('.fixed')
    if (backdrop) {
      await userEvent.click(backdrop)
      expect(onClose).toHaveBeenCalledOnce()
    }
  })
})
