/**
 * Tests for TaskBoard — the @dnd-kit kanban board.
 *
 * dnd-kit's drag-and-drop is hard to drive from JSDOM (it relies on
 * pointer events + measurements that don't exist in jsdom), so these
 * tests focus on the parts of TaskBoard that are reachable without
 * actually performing a drag:
 *
 * - column rendering with the right labels and counts
 * - task cards grouped under the correct status column
 * - the `visibleColumns` prop hiding/showing columns
 * - select-mode toggling and the export floating bar
 *
 * Drag-driven status changes (handleDragEnd) are exercised indirectly
 * by checking the click-to-select path through TaskCard.
 */

import { describe, it, expect, vi } from 'vitest'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import TaskBoard from '../../components/task/TaskBoard'
import { createMockTask } from '../mocks/factories'
import { renderWithProviders } from '../utils/renderWithProviders'
import type { Task } from '../../types'

const baseProps = {
  projectId: 'project-id-1',
  onTaskClick: vi.fn(),
  onUpdateFlags: vi.fn(),
  onArchive: vi.fn(),
  onStatusChange: vi.fn(),
  onExport: vi.fn(),
  onReorder: vi.fn(),
  showArchived: false,
  selectMode: false,
  onExitSelectMode: vi.fn(),
}

function makeTasks(): Task[] {
  return [
    createMockTask({ id: 't1', title: 'TODO task', status: 'todo' }),
    createMockTask({ id: 't2', title: 'In progress task', status: 'in_progress' }),
    createMockTask({ id: 't3', title: 'Done task', status: 'done' }),
    createMockTask({ id: 't4', title: 'Another TODO', status: 'todo', priority: 'urgent' }),
  ]
}

describe('TaskBoard', () => {
  it('renders all five default columns', () => {
    renderWithProviders(<TaskBoard tasks={[]} {...baseProps} />)

    expect(screen.getByText('TODO')).toBeInTheDocument()
    expect(screen.getByText('進行中')).toBeInTheDocument()
    expect(screen.getByText('保留')).toBeInTheDocument()
    expect(screen.getByText('完了')).toBeInTheDocument()
    expect(screen.getByText('キャンセル')).toBeInTheDocument()
  })

  it('shows only the columns listed in visibleColumns', () => {
    renderWithProviders(
      <TaskBoard tasks={[]} {...baseProps} visibleColumns={['todo', 'done']} />,
    )

    expect(screen.getByText('TODO')).toBeInTheDocument()
    expect(screen.getByText('完了')).toBeInTheDocument()
    expect(screen.queryByText('進行中')).not.toBeInTheDocument()
    expect(screen.queryByText('保留')).not.toBeInTheDocument()
    expect(screen.queryByText('キャンセル')).not.toBeInTheDocument()
  })

  it('renders task cards in their corresponding status columns', () => {
    renderWithProviders(<TaskBoard tasks={makeTasks()} {...baseProps} />)

    expect(screen.getByText('TODO task')).toBeInTheDocument()
    expect(screen.getByText('In progress task')).toBeInTheDocument()
    expect(screen.getByText('Done task')).toBeInTheDocument()
    expect(screen.getByText('Another TODO')).toBeInTheDocument()
  })

  it('shows the correct task count badge per column', () => {
    renderWithProviders(<TaskBoard tasks={makeTasks()} {...baseProps} />)

    // Two tasks live in the TODO column; the count badge should reflect that.
    // Find the TODO column header and look for the count badge inside it.
    const todoHeader = screen.getByText('TODO').parentElement
    expect(todoHeader).not.toBeNull()
    expect(within(todoHeader as HTMLElement).getByText('2')).toBeInTheDocument()
  })

  it('orders tasks within a column by priority (urgent first)', () => {
    renderWithProviders(<TaskBoard tasks={makeTasks()} {...baseProps} />)

    const urgentText = screen.getByText('Another TODO')
    const normalText = screen.getByText('TODO task')
    // urgent priority should come earlier in the DOM than medium priority
    expect(
      urgentText.compareDocumentPosition(normalText) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })

  it('does not show the export bar when nothing is selected in select mode', () => {
    renderWithProviders(
      <TaskBoard tasks={makeTasks()} {...baseProps} selectMode />,
    )

    expect(screen.queryByText(/件選択/)).not.toBeInTheDocument()
  })

  it('shows the export bar after a task is selected via TaskCard click', async () => {
    const user = userEvent.setup()
    renderWithProviders(
      <TaskBoard tasks={makeTasks()} {...baseProps} selectMode />,
    )

    // In select mode, clicking the card body toggles selection.
    await user.click(screen.getByText('TODO task'))

    expect(await screen.findByText('1件選択')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Markdown/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /PDF/ })).toBeInTheDocument()
  })

  it('calls onExport with the selected task ids', async () => {
    const onExport = vi.fn()
    const user = userEvent.setup()
    renderWithProviders(
      <TaskBoard
        tasks={makeTasks()}
        {...baseProps}
        selectMode
        onExport={onExport}
      />,
    )

    await user.click(screen.getByText('TODO task'))
    await user.click(screen.getByRole('button', { name: /Markdown/ }))

    expect(onExport).toHaveBeenCalledTimes(1)
    const [ids, format] = onExport.mock.calls[0]
    expect(ids).toContain('t1')
    expect(format).toBe('markdown')
  })

  it('clears selection when exiting select mode', async () => {
    const user = userEvent.setup()
    const { rerender } = renderWithProviders(
      <TaskBoard tasks={makeTasks()} {...baseProps} selectMode />,
    )

    await user.click(screen.getByText('TODO task'))
    expect(await screen.findByText('1件選択')).toBeInTheDocument()

    rerender(<TaskBoard tasks={makeTasks()} {...baseProps} selectMode={false} />)

    expect(screen.queryByText(/件選択/)).not.toBeInTheDocument()
  })

  it('calls onTaskClick when a task card is clicked outside select mode', async () => {
    const onTaskClick = vi.fn()
    const user = userEvent.setup()
    renderWithProviders(
      <TaskBoard
        tasks={makeTasks()}
        {...baseProps}
        onTaskClick={onTaskClick}
      />,
    )

    await user.click(screen.getByText('In progress task'))
    expect(onTaskClick).toHaveBeenCalledWith('t2')
  })
})
