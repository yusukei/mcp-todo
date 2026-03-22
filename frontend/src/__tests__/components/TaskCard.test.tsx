import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import TaskCard from '../../components/task/TaskCard'

const baseTask = {
  id: 'task-1',
  title: 'Sample Task',
  priority: 'medium',
  status: 'todo',
  due_date: null,
  assignee_id: null,
  tags: [],
}

describe('TaskCard', () => {
  it('タイトルを描画する', () => {
    render(<TaskCard task={baseTask} onClick={() => {}} />)
    expect(screen.getByText('Sample Task')).toBeInTheDocument()
  })

  it('優先度ラベルを描画する', () => {
    render(<TaskCard task={baseTask} onClick={() => {}} />)
    expect(screen.getByText('中')).toBeInTheDocument()
  })

  it('urgent 優先度の場合に "緊急" を表示', () => {
    const task = { ...baseTask, priority: 'urgent' }
    render(<TaskCard task={task} onClick={() => {}} />)
    expect(screen.getByText('緊急')).toBeInTheDocument()
  })

  it('due_date がない場合にカレンダーアイコンを表示しない', () => {
    render(<TaskCard task={baseTask} onClick={() => {}} />)
    // Calendar アイコン付き日付テキストが存在しない
    expect(screen.queryByRole('img', { name: /calendar/i })).not.toBeInTheDocument()
  })

  it('due_date がある場合に日付を表示する', () => {
    const task = {
      ...baseTask,
      due_date: '2030-12-31T00:00:00Z',
      status: 'todo',
    }
    render(<TaskCard task={task} onClick={() => {}} />)
    // 日付フォーマット済みテキストが存在する
    expect(screen.getByText(/12月|31/)).toBeInTheDocument()
  })

  it('期限切れタスク (due_date が過去かつ status !== done) の場合に赤色クラスが適用される', () => {
    const task = {
      ...baseTask,
      due_date: '2020-01-01T00:00:00Z', // 過去
      status: 'todo',
    }
    const { container } = render(<TaskCard task={task} onClick={() => {}} />)
    expect(container.querySelector('.text-red-500')).toBeInTheDocument()
  })

  it('done タスクは期限切れ表示にならない', () => {
    const task = {
      ...baseTask,
      due_date: '2020-01-01T00:00:00Z',
      status: 'done',
    }
    const { container } = render(<TaskCard task={task} onClick={() => {}} />)
    expect(container.querySelector('.text-red-500')).not.toBeInTheDocument()
  })

  it('タグを描画する', () => {
    const task = { ...baseTask, tags: ['bug', 'frontend'] }
    render(<TaskCard task={task} onClick={() => {}} />)
    expect(screen.getByText('bug')).toBeInTheDocument()
    expect(screen.getByText('frontend')).toBeInTheDocument()
  })

  it('クリック時に onClick コールバックが呼ばれる', async () => {
    const onClick = vi.fn()
    render(<TaskCard task={baseTask} onClick={onClick} />)
    await userEvent.click(screen.getByText('Sample Task'))
    expect(onClick).toHaveBeenCalledOnce()
  })
})
