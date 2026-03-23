import React, { useState, useMemo, useCallback } from 'react'
import clsx from 'clsx'
import { Archive, ArchiveRestore, Calendar, CornerDownRight, HelpCircle } from 'lucide-react'
import type { Task } from '../../types'
import { STATUS_LABELS, STATUS_COLORS, PRIORITY_DOT_COLORS } from '../../constants/task'

interface TaskRowProps {
  task: Task
  isSubtask: boolean
  isSelected: boolean
  onTaskClick: (id: string) => void
  onToggleSelect: (id: string) => void
  onUpdateFlags: (taskId: string, flags: { needs_detail?: boolean; approved?: boolean }) => void
  onArchive: (taskId: string, archive: boolean) => void
}

const TaskRow = React.memo(function TaskRow({
  task,
  isSubtask,
  isSelected,
  onTaskClick,
  onToggleSelect,
  onUpdateFlags,
  onArchive,
}: TaskRowProps) {
  const isOverdue = task.due_date && new Date(task.due_date) < new Date() && task.status !== 'done'

  return (
    <div
      onClick={() => onTaskClick(task.id)}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onTaskClick(task.id) } }}
      role="button"
      tabIndex={0}
      className={clsx(
        'flex items-center gap-4 px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-700/50 cursor-pointer',
        task.archived && 'opacity-60',
        isSelected && 'bg-indigo-50/50 dark:bg-indigo-900/20',
        isSubtask && 'pl-10',
      )}
    >
      <label className="flex items-center flex-shrink-0 cursor-pointer" onClick={(e) => e.stopPropagation()}>
        <input
          type="checkbox"
          checked={isSelected}
          onChange={() => onToggleSelect(task.id)}
          className="rounded border-gray-300 dark:border-gray-600 text-indigo-600 focus:ring-indigo-500 w-3.5 h-3.5"
        />
      </label>
      {isSubtask && (
        <CornerDownRight className="w-3.5 h-3.5 text-gray-400 dark:text-gray-500 flex-shrink-0 -ml-2" />
      )}
      <span className={clsx('w-2 h-2 rounded-full flex-shrink-0', PRIORITY_DOT_COLORS[task.priority])} />
      {task.task_type === 'decision' && (
        <span className="flex items-center gap-0.5 text-xs px-1.5 py-0.5 rounded-full font-medium whitespace-nowrap bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-400 flex-shrink-0">
          <HelpCircle className="w-3 h-3" />
          要判断
        </span>
      )}
      <span className="flex-1 text-sm text-gray-800 dark:text-gray-100 font-medium">{task.title}</span>
      {task.tags && task.tags.length > 0 && (
        <div className="flex items-center gap-1 flex-shrink-0">
          {task.tags.slice(0, 2).map((tag: string) => (
            <span key={tag} className="text-xs bg-indigo-50 dark:bg-indigo-900/40 text-indigo-600 dark:text-indigo-400 px-2 py-0.5 rounded-full">
              {tag}
            </span>
          ))}
        </div>
      )}
      <div className="flex items-center gap-3 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
        <label className="flex items-center gap-1 text-xs text-amber-700 dark:text-amber-400 cursor-pointer">
          <input
            type="checkbox"
            checked={task.needs_detail}
            onChange={(e) => onUpdateFlags(task.id, {
              needs_detail: e.target.checked,
              ...(e.target.checked ? { approved: false } : {}),
            })}
            className="rounded border-amber-300 text-amber-600 focus:ring-amber-500 w-3.5 h-3.5"
          />
          詳細要求
        </label>
        <label className="flex items-center gap-1 text-xs text-emerald-700 dark:text-emerald-400 cursor-pointer">
          <input
            type="checkbox"
            checked={task.approved}
            onChange={(e) => onUpdateFlags(task.id, {
              approved: e.target.checked,
              ...(e.target.checked ? { needs_detail: false } : {}),
            })}
            className="rounded border-emerald-300 text-emerald-600 focus:ring-emerald-500 w-3.5 h-3.5"
          />
          実行許可
        </label>
      </div>
      <div className="flex items-center gap-3 flex-shrink-0">
        {task.due_date && (
          <span className={clsx('flex items-center gap-1 text-xs', isOverdue ? 'text-red-500 dark:text-red-400' : 'text-gray-400 dark:text-gray-500')}>
            <Calendar className="w-3 h-3" />
            {new Date(task.due_date).toLocaleDateString('ja-JP', { month: 'short', day: 'numeric' })}
          </span>
        )}
        <span className={clsx('text-xs px-2 py-0.5 rounded-full', STATUS_COLORS[task.status])}>
          {STATUS_LABELS[task.status]}
        </span>
        {(task.status === 'done' || task.status === 'cancelled') && (
          <button
            onClick={(e) => {
              e.stopPropagation()
              onArchive(task.id, !task.archived)
            }}
            className={clsx(
              'p-1 rounded transition-colors',
              task.archived
                ? 'text-indigo-500 dark:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-900/30'
                : 'text-gray-400 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700',
            )}
            title={task.archived ? 'アーカイブ解除' : 'アーカイブ'}
          >
            {task.archived ? <ArchiveRestore className="w-4 h-4" /> : <Archive className="w-4 h-4" />}
          </button>
        )}
      </div>
    </div>
  )
})

interface Props {
  tasks: Task[]
  projectId: string
  onTaskClick: (id: string) => void
  onUpdateFlags: (taskId: string, flags: { needs_detail?: boolean; approved?: boolean }) => void
  onArchive: (taskId: string, archive: boolean) => void
  showArchived: boolean
}

export default function TaskList({ tasks, projectId, onTaskClick, onUpdateFlags, onArchive, showArchived }: Props) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())

  const allSelected = tasks.length > 0 && selectedIds.size === tasks.length
  const someSelected = selectedIds.size > 0 && selectedIds.size < tasks.length

  const toggleSelectAll = () => {
    if (allSelected) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(tasks.map((t) => t.id)))
    }
  }

  const toggleSelect = useCallback((taskId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(taskId)) {
        next.delete(taskId)
      } else {
        next.add(taskId)
      }
      return next
    })
  }, [])

  const bulkUpdateFlags = (flags: { needs_detail?: boolean; approved?: boolean }) => {
    for (const taskId of selectedIds) {
      onUpdateFlags(taskId, flags)
    }
    setSelectedIds(new Set())
  }

  // Build hierarchical list: parent tasks followed by their subtasks
  const orderedTasks = useMemo(() => {
    const subtaskIds = new Set(
      tasks.filter((t) => t.parent_task_id).map((t) => t.id)
    )
    const subtasksByParent = new Map<string, Task[]>()
    for (const t of tasks) {
      if (t.parent_task_id) {
        const existing = subtasksByParent.get(t.parent_task_id) ?? []
        existing.push(t)
        subtasksByParent.set(t.parent_task_id, existing)
      }
    }

    const result: { task: Task; isSubtask: boolean }[] = []
    for (const t of tasks) {
      if (subtaskIds.has(t.id)) continue // skip subtasks in top-level pass
      result.push({ task: t, isSubtask: false })
      const children = subtasksByParent.get(t.id)
      if (children) {
        for (const child of children) {
          result.push({ task: child, isSubtask: true })
        }
      }
    }
    return result
  }, [tasks])

  return (
    <div className="p-6 overflow-y-auto h-full">
      <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 divide-y divide-gray-100 dark:divide-gray-700">
        {/* Header with select-all and bulk actions */}
        {tasks.length > 0 && (
          <div className="flex items-center gap-3 px-4 py-2 bg-gray-50 dark:bg-gray-800/80">
            <label className="flex items-center cursor-pointer" onClick={(e) => e.stopPropagation()}>
              <input
                type="checkbox"
                checked={allSelected}
                ref={(el) => { if (el) el.indeterminate = someSelected }}
                onChange={toggleSelectAll}
                className="rounded border-gray-300 dark:border-gray-600 text-indigo-600 focus:ring-indigo-500 w-3.5 h-3.5"
              />
            </label>
            {selectedIds.size > 0 ? (
              <div className="flex items-center gap-2 flex-1">
                <span className="text-xs text-gray-500 dark:text-gray-400">
                  {selectedIds.size}件選択
                </span>
                <div className="flex flex-wrap items-center gap-2 ml-2">
                  <button
                    onClick={() => bulkUpdateFlags({ needs_detail: true, approved: false })}
                    className="text-xs px-2 py-1 rounded bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-400 hover:bg-amber-200 dark:hover:bg-amber-900/60 transition-colors"
                  >
                    詳細要求 ON
                  </button>
                  <button
                    onClick={() => bulkUpdateFlags({ needs_detail: false })}
                    className="text-xs px-2 py-1 rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
                  >
                    詳細要求 OFF
                  </button>
                  <button
                    onClick={() => bulkUpdateFlags({ approved: true, needs_detail: false })}
                    className="text-xs px-2 py-1 rounded bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-400 hover:bg-emerald-200 dark:hover:bg-emerald-900/60 transition-colors"
                  >
                    実行許可 ON
                  </button>
                  <button
                    onClick={() => bulkUpdateFlags({ approved: false })}
                    className="text-xs px-2 py-1 rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
                  >
                    実行許可 OFF
                  </button>
                  <button
                    onClick={() => {
                      for (const taskId of selectedIds) {
                        onArchive(taskId, true)
                      }
                      setSelectedIds(new Set())
                    }}
                    className="text-xs px-2 py-1 rounded bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-400 hover:bg-indigo-200 dark:hover:bg-indigo-900/60 transition-colors"
                  >
                    アーカイブ
                  </button>
                </div>
              </div>
            ) : (
              <span className="text-xs text-gray-400 dark:text-gray-500">一括操作</span>
            )}
          </div>
        )}
        {tasks.length === 0 && (
          <div className="py-16 text-center text-gray-400 dark:text-gray-500">タスクがありません</div>
        )}
        {orderedTasks.map(({ task, isSubtask }) => (
          <TaskRow
            key={task.id}
            task={task}
            isSubtask={isSubtask}
            isSelected={selectedIds.has(task.id)}
            onTaskClick={onTaskClick}
            onToggleSelect={toggleSelect}
            onUpdateFlags={onUpdateFlags}
            onArchive={onArchive}
          />
        ))}
      </div>
    </div>
  )
}
