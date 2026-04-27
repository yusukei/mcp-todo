import { useCallback, useState } from 'react'
import { CheckSquare, X } from 'lucide-react'
import TaskDetail from '../../components/task/TaskDetail'
import type { PaneComponentProps } from '../paneRegistry'
import { useWorkbenchEvent } from '../eventBus'

/**
 * TaskDetail pane (Phase C2 D1-b). Displays a single task in
 * Workbench-native mode (no slide-over backdrop). The current
 * ``taskId`` is stored in ``paneConfig.taskId`` so a reload picks up
 * where the user left off; the pane subscribes to the
 * ``open-task`` event so a click in the TasksPane updates this pane
 * (Decision §5.3, §5.6.1 I-1: focused TaskDetailPane is the routing
 * target).
 *
 * Empty state: when no task is selected (initial mount of a fresh
 * pane), shows a placeholder asking the user to click a task in the
 * TasksPane. The pane never auto-picks a task — that would surprise
 * the user.
 */
export default function TaskDetailPane({
  paneId,
  projectId,
  paneConfig,
  onConfigChange,
}: PaneComponentProps<'task-detail'>) {
  const config = paneConfig
  const taskId = config.taskId

  // Subscribe to cross-pane open-task events. The bus picks the
  // focused / most-recently-focused / first TaskDetailPane (§5.3),
  // so multiple panes can coexist with each acting independently
  // when not focused (§5.6.1 I-1).
  useWorkbenchEvent(paneId, 'open-task', ({ taskId: nextId }) => {
    if (!nextId || nextId === taskId) return
    onConfigChange({ taskId: nextId })
  })

  // Local "navigate to another task" handler — used by the existing
  // TaskDetail subtask / link sections. Same flow as open-task.
  const navigateTask = useCallback(
    (next: string | null) => {
      if (!next) return
      onConfigChange({ taskId: next })
    },
    [onConfigChange],
  )

  // "Close" inside a pane just clears the selection (the pane itself
  // stays open so the user can pick another task). Removing the pane
  // requires the tab close button.
  const handleClose = useCallback(() => {
    onConfigChange({ taskId: undefined })
  }, [onConfigChange])

  if (!taskId) {
    return <EmptyState />
  }

  return (
    <TaskDetail
      // Re-key on taskId so React tears down internal state (editing
      // flags, draft text, attachment previews) when switching tasks
      // — otherwise the previous task's draft would leak into the new
      // one.
      key={taskId}
      taskId={taskId}
      projectId={projectId}
      onClose={handleClose}
      onNavigateTask={navigateTask}
      displayMode="pane"
      metaRail
    />
  )
}

function EmptyState() {
  return (
    <div className="h-full flex flex-col items-center justify-center gap-3 p-6 text-center text-gray-500 dark:text-gray-400">
      <CheckSquare className="w-10 h-10 text-gray-300 dark:text-gray-600" />
      <p className="text-sm font-medium">タスクを選択してください</p>
      <p className="text-xs max-w-xs">
        Tasks pane でタスクをクリックすると、ここに詳細が表示されます。
        他に Task Detail pane が無い時は焦点 (focused) のあるこのペイン
        にルーティングされます。
      </p>
      <p className="text-[11px] text-gray-400 dark:text-gray-500 flex items-center gap-1">
        <X className="w-3 h-3" />
        ペイン自体を閉じるにはタブの × を使ってください
      </p>
    </div>
  )
}
