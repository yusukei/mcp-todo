import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import TaskList from '../../components/task/TaskList'
import type { Task } from '../../types'

const baseTasks: Task[] = [
  {
    id: 'task-1',
    project_id: 'project-1',
    title: 'First Task',
    description: null,
    status: 'todo',
    priority: 'medium',
    due_date: null,
    assignee_id: null,
    parent_task_id: null,
    tags: [],
    comments: [],
    is_deleted: false,
    archived: false,
    completed_at: null,
    needs_detail: false,
    approved: false,
    created_by: 'user-1',
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    sort_order: 0,
  },
  {
    id: 'task-2',
    project_id: 'project-1',
    title: 'Second Task',
    description: null,
    status: 'in_progress',
    priority: 'high',
    due_date: null,
    assignee_id: null,
    parent_task_id: null,
    tags: [],
    comments: [],
    is_deleted: false,
    archived: false,
    completed_at: null,
    needs_detail: false,
    approved: false,
    created_by: 'user-1',
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    sort_order: 1,
  },
]

const defaultProps = {
  projectId: 'project-1',
  selectMode: true,
  onTaskClick: vi.fn(),
  onUpdateFlags: vi.fn(),
  onArchive: vi.fn(),
  onBatchUpdateFlags: vi.fn(),
  onBatchArchive: vi.fn(),
  onExport: vi.fn(),
  onReorder: vi.fn(),
  showArchived: false,
}

describe('TaskList', () => {
  it('タスクのタイトルを描画する', () => {
    render(<TaskList tasks={baseTasks} {...defaultProps} />)
    expect(screen.getByText('First Task')).toBeInTheDocument()
    expect(screen.getByText('Second Task')).toBeInTheDocument()
  })

  it('タスクがない場合に空状態メッセージを表示する', () => {
    render(<TaskList tasks={[]} {...defaultProps} />)
    expect(screen.getByText('タスクがありません')).toBeInTheDocument()
  })

  it('タスク行クリック時に onTaskClick が呼ばれる', async () => {
    const onTaskClick = vi.fn()
    render(<TaskList tasks={baseTasks} {...defaultProps} onTaskClick={onTaskClick} />)
    await userEvent.click(screen.getByText('First Task'))
    expect(onTaskClick).toHaveBeenCalledWith('task-1')
  })

  it('ステータスバッジが正しいラベルを表示する', () => {
    render(<TaskList tasks={baseTasks} {...defaultProps} />)
    expect(screen.getByText('TODO')).toBeInTheDocument()
    expect(screen.getByText('進行中')).toBeInTheDocument()
  })

  it('期限切れタスクに赤色スタイルが適用される', () => {
    const overdueTasks: Task[] = [
      {
        ...baseTasks[0],
        due_date: '2020-01-01T00:00:00Z',
        status: 'todo',
      },
    ]
    const { container } = render(
      <TaskList tasks={overdueTasks} {...defaultProps} />
    )
    expect(container.querySelector('.text-red-500')).toBeInTheDocument()
  })

  it('done のタスクは期限切れ表示にならない', () => {
    const doneTasks: Task[] = [
      {
        ...baseTasks[0],
        due_date: '2020-01-01T00:00:00Z',
        status: 'done',
      },
    ]
    const { container } = render(
      <TaskList tasks={doneTasks} {...defaultProps} />
    )
    expect(container.querySelector('.text-red-500')).not.toBeInTheDocument()
  })

  describe('一括操作', () => {
    it('selectMode=true の場合に一括操作ヘッダーが表示される', () => {
      render(<TaskList tasks={baseTasks} {...defaultProps} selectMode={true} />)
      expect(screen.getByText('一括操作')).toBeInTheDocument()
    })

    it('selectMode=false の場合に一括操作ヘッダーが表示されない', () => {
      render(<TaskList tasks={baseTasks} {...defaultProps} selectMode={false} />)
      expect(screen.queryByText('一括操作')).not.toBeInTheDocument()
    })

    it('タスクがない場合に一括操作ヘッダーが表示されない', () => {
      render(<TaskList tasks={[]} {...defaultProps} />)
      expect(screen.queryByText('一括操作')).not.toBeInTheDocument()
    })

    it('全選択チェックボックスで全タスクが選択される', async () => {
      render(<TaskList tasks={baseTasks} {...defaultProps} />)
      const checkboxes = screen.getAllByRole('checkbox')
      // First checkbox is select-all, followed by per-task selection checkboxes and flag checkboxes
      const selectAllCheckbox = checkboxes[0]
      await userEvent.click(selectAllCheckbox)
      expect(screen.getByText('2件選択')).toBeInTheDocument()
    })

    it('全選択後に再度クリックで選択解除される', async () => {
      render(<TaskList tasks={baseTasks} {...defaultProps} selectMode={true} />)
      const checkboxes = screen.getAllByRole('checkbox')
      const selectAllCheckbox = checkboxes[0]
      await userEvent.click(selectAllCheckbox)
      expect(screen.getByText('2件選択')).toBeInTheDocument()
      await userEvent.click(selectAllCheckbox)
      expect(screen.getByText('一括操作')).toBeInTheDocument()
    })

    it('selectMode=true で一括操作ボタンが常に表示される', () => {
      render(<TaskList tasks={baseTasks} {...defaultProps} selectMode={true} />)
      expect(screen.getByText('実行許可 ON')).toBeInTheDocument()
      expect(screen.getByText('実行許可 OFF')).toBeInTheDocument()
    })

    it('実行許可 ON ボタンで onBatchUpdateFlags が呼ばれる', async () => {
      const onBatchUpdateFlags = vi.fn()
      render(<TaskList tasks={baseTasks} {...defaultProps} onBatchUpdateFlags={onBatchUpdateFlags} />)
      const checkboxes = screen.getAllByRole('checkbox')
      await userEvent.click(checkboxes[0]) // select all
      await userEvent.click(screen.getByText('実行許可 ON'))
      expect(onBatchUpdateFlags).toHaveBeenCalledTimes(1)
      expect(onBatchUpdateFlags).toHaveBeenCalledWith(['task-1', 'task-2'], { approved: true })
    })

    it('一括操作後に選択がクリアされる', async () => {
      const onBatchUpdateFlags = vi.fn()
      render(<TaskList tasks={baseTasks} {...defaultProps} selectMode={true} onBatchUpdateFlags={onBatchUpdateFlags} />)
      const checkboxes = screen.getAllByRole('checkbox')
      await userEvent.click(checkboxes[0]) // select all
      expect(screen.getByText('2件選択')).toBeInTheDocument()
      await userEvent.click(screen.getByText('実行許可 ON'))
      expect(screen.getByText('一括操作')).toBeInTheDocument()
    })

    it('個別タスクの選択チェックボックスが機能する', async () => {
      render(<TaskList tasks={baseTasks} {...defaultProps} />)
      const checkboxes = screen.getAllByRole('checkbox')
      // checkboxes[0] = select-all, checkboxes[1] = task-1 selection, checkboxes[2] = task-2 selection
      await userEvent.click(checkboxes[1]) // select task-1 only
      expect(screen.getByText('1件選択')).toBeInTheDocument()
    })
  })
})
