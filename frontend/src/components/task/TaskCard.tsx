import { Archive, ArchiveRestore, Calendar, User, CornerDownRight, HelpCircle } from 'lucide-react'
import clsx from 'clsx'
import type { Task } from '../../types'
import { PRIORITY_COLORS, PRIORITY_LABELS } from '../../constants/task'

interface Props {
  task: Task
  onClick: () => void
  onUpdateFlags: (taskId: string, flags: { needs_detail?: boolean; approved?: boolean }) => void
  onArchive?: (taskId: string, archive: boolean) => void
}

export default function TaskCard({ task, onClick, onUpdateFlags, onArchive }: Props) {
  const isOverdue = task.due_date && new Date(task.due_date) < new Date() && task.status !== 'done' && task.status !== 'cancelled'

  return (
    <div
      onClick={onClick}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick() } }}
      role="button"
      tabIndex={0}
      className={clsx(
        'bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-3 cursor-pointer hover:shadow-sm hover:border-indigo-300 dark:hover:border-indigo-600 transition-all',
        task.archived && 'opacity-60',
        isOverdue && 'border-l-4 border-l-red-500 dark:border-l-red-400',
      )}
    >
      {task.parent_task_id && (
        <div className="flex items-center gap-1 mb-1">
          <CornerDownRight className="w-3 h-3 text-gray-400 dark:text-gray-500" />
          <span className="text-xs text-gray-400 dark:text-gray-500">サブタスク</span>
        </div>
      )}
      <div className="flex items-start gap-1.5 mb-2">
        <span className={clsx('text-xs px-1.5 py-0.5 rounded-full font-medium whitespace-nowrap mt-0.5', PRIORITY_COLORS[task.priority])}>
          {PRIORITY_LABELS[task.priority]}
        </span>
        {task.task_type === 'decision' && (
          <span className="flex items-center gap-0.5 text-xs px-1.5 py-0.5 rounded-full font-medium whitespace-nowrap mt-0.5 bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-400">
            <HelpCircle className="w-3 h-3" />
            要判断
          </span>
        )}
        <p className="text-sm font-medium text-gray-800 dark:text-gray-100 line-clamp-2">{task.title}</p>
      </div>

      <div className="flex items-center gap-3 mb-2" onClick={(e) => e.stopPropagation()}>
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

      <div className="flex flex-wrap gap-1 mb-2">
        {task.tags?.map((tag: string) => (
          <span key={tag} className="text-xs bg-indigo-50 dark:bg-indigo-900/40 text-indigo-600 dark:text-indigo-400 px-2 py-0.5 rounded-full">
            {tag}
          </span>
        ))}
      </div>

      <div className="flex items-center justify-end mt-2">
        <div className="flex items-center gap-2">
          {task.due_date && (
            <span className={clsx('flex items-center gap-1 text-xs', isOverdue ? 'text-red-500 dark:text-red-400' : 'text-gray-400 dark:text-gray-500')}>
              <Calendar className="w-3 h-3" />
              {new Date(task.due_date).toLocaleDateString('ja-JP', { month: 'short', day: 'numeric' })}
            </span>
          )}
          {task.assignee_id && (
            <span className="flex items-center gap-1 text-xs text-gray-400 dark:text-gray-500">
              <User className="w-3 h-3" />
            </span>
          )}
          {onArchive && (task.status === 'done' || task.status === 'cancelled') && (
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
              {task.archived ? <ArchiveRestore className="w-3.5 h-3.5" /> : <Archive className="w-3.5 h-3.5" />}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
