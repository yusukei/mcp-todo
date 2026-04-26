import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import TaskCard from '../../components/task/TaskCard'
import { createMockTask } from '../mocks/factories'

const baseTask = createMockTask({
  id: 'task-1',
  title: 'Sample Task',
})

describe('TaskCard', () => {
  it('タイトルを描画する', () => {
    render(<TaskCard task={baseTask} onClick={() => {}} onUpdateFlags={() => {}} />)
    expect(screen.getByText('Sample Task')).toBeInTheDocument()
  })

  it('優先度ドットを描画する (medium = pri-medium 黄)', () => {
    render(<TaskCard task={baseTask} onClick={() => {}} onUpdateFlags={() => {}} />)
    // ドットは aria-label / title で priority を表現する
    const dot = screen.getByLabelText(/優先度: 中/)
    expect(dot).toBeInTheDocument()
    expect(dot.className).toContain('bg-pri-medium')
  })

  it('urgent 優先度の場合にドットが pri-urgent (pink) になる', () => {
    const task = createMockTask({ ...baseTask, priority: 'urgent' })
    render(<TaskCard task={task} onClick={() => {}} onUpdateFlags={() => {}} />)
    const dot = screen.getByLabelText(/優先度: 緊急/)
    expect(dot).toBeInTheDocument()
    expect(dot.className).toContain('bg-pri-urgent')
  })

  it('due_date がない場合にカレンダーアイコンを表示しない', () => {
    render(<TaskCard task={baseTask} onClick={() => {}} onUpdateFlags={() => {}} />)
    // Calendar アイコン付き日付テキストが存在しない
    expect(screen.queryByRole('img', { name: /calendar/i })).not.toBeInTheDocument()
  })

  it('due_date がある場合に日付を表示する', () => {
    const task = createMockTask({
      ...baseTask,
      due_date: '2030-12-31T00:00:00Z',
      status: 'todo',
    })
    render(<TaskCard task={task} onClick={() => {}} onUpdateFlags={() => {}} />)
    // 日付フォーマット済みテキストが存在する
    expect(screen.getByText(/12月|31/)).toBeInTheDocument()
  })

  it('期限切れタスク (due_date が過去かつ status !== done) の場合に urgent 色クラスが適用される', () => {
    const task = createMockTask({
      ...baseTask,
      due_date: '2020-01-01T00:00:00Z', // 過去
      status: 'todo',
    })
    const { container } = render(<TaskCard task={task} onClick={() => {}} onUpdateFlags={() => {}} />)
    // Phase 4: Monokai trade — overdue は border-l-pri-urgent + text-pri-urgent
    expect(container.querySelector('.text-pri-urgent')).toBeInTheDocument()
  })

  it('done タスクは期限切れ表示にならない', () => {
    const task = createMockTask({
      ...baseTask,
      due_date: '2020-01-01T00:00:00Z',
      status: 'done',
    })
    const { container } = render(<TaskCard task={task} onClick={() => {}} onUpdateFlags={() => {}} />)
    expect(container.querySelector('.text-pri-urgent')).not.toBeInTheDocument()
  })

  it('タグを描画する', () => {
    const task = createMockTask({ ...baseTask, tags: ['bug', 'frontend'] })
    render(<TaskCard task={task} onClick={() => {}} onUpdateFlags={() => {}} />)
    expect(screen.getByText('bug')).toBeInTheDocument()
    expect(screen.getByText('frontend')).toBeInTheDocument()
  })

  it('クリック時に onClick コールバックが呼ばれる', async () => {
    const onClick = vi.fn()
    render(<TaskCard task={baseTask} onClick={onClick} onUpdateFlags={() => {}} />)
    await userEvent.click(screen.getByText('Sample Task'))
    expect(onClick).toHaveBeenCalledOnce()
  })

  it('実行許可トグルボタンを描画する', () => {
    render(<TaskCard task={baseTask} onClick={() => {}} onUpdateFlags={() => {}} />)
    expect(screen.getByText('実行許可')).toBeInTheDocument()
    expect(screen.getByLabelText('実行許可を付与')).toBeInTheDocument()
  })

  it('トグルボタンクリック時に onClick が呼ばれない', async () => {
    const onClick = vi.fn()
    const onUpdateFlags = vi.fn()
    render(<TaskCard task={baseTask} onClick={onClick} onUpdateFlags={onUpdateFlags} />)
    await userEvent.click(screen.getByLabelText('実行許可を付与'))
    expect(onClick).not.toHaveBeenCalled()
    expect(onUpdateFlags).toHaveBeenCalled()
  })

  it('実行許可トグルで onUpdateFlags が正しい引数で呼ばれる', async () => {
    const onUpdateFlags = vi.fn()
    render(<TaskCard task={baseTask} onClick={() => {}} onUpdateFlags={onUpdateFlags} />)
    await userEvent.click(screen.getByLabelText('実行許可を付与'))
    expect(onUpdateFlags).toHaveBeenCalledWith('task-1', {
      approved: true,
    })
  })

  it('approved=true の場合に取消ラベルが表示される', () => {
    const task = createMockTask({ ...baseTask, approved: true })
    render(<TaskCard task={task} onClick={() => {}} onUpdateFlags={() => {}} />)
    expect(screen.getByLabelText('実行許可を取消')).toBeInTheDocument()
  })
})
