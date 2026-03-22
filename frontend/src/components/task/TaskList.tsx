import { useState } from 'react'
import clsx from 'clsx'
import { Archive, ArchiveRestore, Calendar } from 'lucide-react'
import type { Task } from '../../types'
import { STATUS_LABELS, STATUS_COLORS, PRIORITY_DOT_COLORS } from '../../constants/task'

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

  const toggleSelect = (taskId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(taskId)) {
        next.delete(taskId)
      } else {
        next.add(taskId)
      }
      return next
    })
  }

  const bulkUpdateFlags = (flags: { needs_detail?: boolean; approved?: boolean }) => {
    for (const taskId of selectedIds) {
      onUpdateFlags(taskId, flags)
    }
    setSelectedIds(new Set())
  }

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
                <div className="flex items-center gap-1.5 ml-2">
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
        {tasks.map((task) => {
          const isOverdue = task.due_date && new Date(task.due_date) < new Date() && task.status !== 'done'
          return (
            <div
              key={task.id}
              onClick={() => onTaskClick(task.id)}
              className={clsx(
                'flex items-center gap-4 px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-700/50 cursor-pointer',
                task.archived && 'opacity-60',
                selectedIds.has(task.id) && 'bg-indigo-50/50 dark:bg-indigo-900/20',
              )}
            >
              <label className="flex items-center flex-shrink-0 cursor-pointer" onClick={(e) => e.stopPropagation()}>
                <input
                  type="checkbox"
                  checked={selectedIds.has(task.id)}
                  onChange={() => toggleSelect(task.id)}
                  className="rounded border-gray-300 dark:border-gray-600 text-indigo-600 focus:ring-indigo-500 w-3.5 h-3.5"
                />
              </label>
              <span className={clsx('w-2 h-2 rounded-full flex-shrink-0', PRIORITY_DOT_COLORS[task.priority])} />
              <span className="flex-1 text-sm text-gray-800 dark:text-gray-100 font-medium">{task.title}</span>
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
                {task.tags?.slice(0, 2).map((tag: string) => (
                  <span key={tag} className="text-xs bg-indigo-50 dark:bg-indigo-900/40 text-indigo-600 dark:text-indigo-400 px-2 py-0.5 rounded-full hidden sm:block">
                    {tag}
                  </span>
                ))}
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
        })}
      </div>
    </div>
  )
}
