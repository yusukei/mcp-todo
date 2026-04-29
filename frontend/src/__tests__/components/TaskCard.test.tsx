/**
 * TaskCard テスト — P0-4 のリライト後構造に追従。
 *
 * 設計プロト準拠でカード上のクイックトグル (実行許可 / アーカイブ /
 * コピー) は撤去された。承認状態は **「approved===true のときだけ
 * 控えめに `許可` を出す」** インジケータに退化したため、トグルの
 * クリック検証はテストとしては成立しない (詳細編集は TaskDetail へ)。
 */
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
    render(<TaskCard task={baseTask} onClick={() => {}} />)
    expect(screen.getByText('Sample Task')).toBeInTheDocument()
  })

  it('優先度ドット (medium = pri-medium 黄)', () => {
    const { container } = render(<TaskCard task={baseTask} onClick={() => {}} />)
    // 5px の小ドットを class 名でマッチ。「中」のラベルは title 属性で
    // 持つ (画面テキストには現れない)。
    const dot = container.querySelector('.bg-pri-medium')
    expect(dot).toBeInTheDocument()
  })

  it('urgent 優先度の場合にドットが pri-urgent', () => {
    const task = createMockTask({ ...baseTask, priority: 'urgent' })
    const { container } = render(<TaskCard task={task} onClick={() => {}} />)
    expect(container.querySelector('.bg-pri-urgent')).toBeInTheDocument()
  })

  it('due_date がない場合に日付テキストが出ない', () => {
    render(<TaskCard task={baseTask} onClick={() => {}} />)
    // 日付フォーマット結果 (M/D) は出ないはず
    expect(screen.queryByText(/^\d{1,2}\/\d{1,2}$/)).not.toBeInTheDocument()
  })

  it('due_date がある場合に M/D で日付を表示する', () => {
    const task = createMockTask({
      ...baseTask,
      due_date: '2030-12-31T00:00:00Z',
      status: 'todo',
    })
    render(<TaskCard task={task} onClick={() => {}} />)
    // toLocaleDateString に依存しない自前の formatShortDate を使うので
    // タイムゾーン差で 30 / 31 のどちらでも通るように緩く判定。
    expect(screen.getByText(/^12\/(30|31)$/)).toBeInTheDocument()
  })

  it('期限切れタスクで urgent カラークラスが付く', () => {
    const task = createMockTask({
      ...baseTask,
      due_date: '2020-01-01T00:00:00Z',
      status: 'todo',
    })
    const { container } = render(<TaskCard task={task} onClick={() => {}} />)
    expect(container.querySelector('.text-pri-urgent')).toBeInTheDocument()
  })

  it('done タスクは期限切れ表示にならない', () => {
    const task = createMockTask({
      ...baseTask,
      due_date: '2020-01-01T00:00:00Z',
      status: 'done',
    })
    const { container } = render(<TaskCard task={task} onClick={() => {}} />)
    expect(container.querySelector('.text-pri-urgent')).not.toBeInTheDocument()
  })

  it('タグを描画する (3 件まで + +N オーバーフロー)', () => {
    const task = createMockTask({
      ...baseTask,
      tags: ['bug', 'frontend', 'design', 'p1'],
    })
    render(<TaskCard task={task} onClick={() => {}} />)
    expect(screen.getByText('bug')).toBeInTheDocument()
    expect(screen.getByText('frontend')).toBeInTheDocument()
    expect(screen.getByText('design')).toBeInTheDocument()
    // 4 件目はオーバーフロー
    expect(screen.queryByText('p1')).not.toBeInTheDocument()
    expect(screen.getByText('+1')).toBeInTheDocument()
  })

  it('クリック時に onClick コールバックが呼ばれる', async () => {
    const onClick = vi.fn()
    render(<TaskCard task={baseTask} onClick={onClick} />)
    await userEvent.click(screen.getByText('Sample Task'))
    expect(onClick).toHaveBeenCalledOnce()
  })

  it('approved=true の場合のみ「許可」インジケータが出る', () => {
    const { rerender } = render(<TaskCard task={baseTask} onClick={() => {}} />)
    expect(screen.queryByText('許可')).not.toBeInTheDocument()

    const approved = createMockTask({ ...baseTask, approved: true })
    rerender(<TaskCard task={approved} onClick={() => {}} />)
    expect(screen.getByText('許可')).toBeInTheDocument()
  })

  it('needs_detail=true の場合に「詳細要求」インジケータが出る', () => {
    const task = createMockTask({ ...baseTask, needs_detail: true })
    render(<TaskCard task={task} onClick={() => {}} />)
    expect(screen.getByText('詳細要求')).toBeInTheDocument()
  })

  it('active_form があるとき進行中バナーを表示する', () => {
    const task = createMockTask({
      ...baseTask,
      status: 'in_progress',
      active_form: 'TerminalPane.tsx を編集中…',
    })
    render(<TaskCard task={task} onClick={() => {}} />)
    expect(
      screen.getByText('TerminalPane.tsx を編集中…'),
    ).toBeInTheDocument()
  })

  it('短縮 ID (T<6hex>) が表示される', () => {
    const task = createMockTask({
      ...baseTask,
      id: '69ee07400d5b906f437662a3',
    })
    render(<TaskCard task={task} onClick={() => {}} />)
    // 末尾 6 桁を大文字
    expect(screen.getByText('T7662A3')).toBeInTheDocument()
  })

  it('CopyUrlButton が右上に出現し、クリックがカード onClick へバブルしない (URL S7)', async () => {
    const onClick = vi.fn()
    // 24 桁 hex でないと buildUrl が throw する。S7 配置先は実 ID を渡す
    // 想定なので test fixture も実形式に揃える。
    const task = createMockTask({
      ...baseTask,
      id: '69ee07400d5b906f437662a3',
      project_id: '69bfffad73ed736a9d13fd0f',
    })
    render(<TaskCard task={task} onClick={onClick} />)
    // axis 5 Reachable: aria-label で button が探せる
    const copyBtn = screen.getByRole('button', { name: /Copy URL to task/ })
    expect(copyBtn).toBeInTheDocument()
    // axis 6 Operable: click は wrapper の stopPropagation でカードへ伝わらない
    await userEvent.click(copyBtn)
    expect(onClick).not.toHaveBeenCalled()
  })
})
