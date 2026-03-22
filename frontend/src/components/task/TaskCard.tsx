import { Calendar, User } from 'lucide-react'
import clsx from 'clsx'
import type { Task } from '../../types'
import { PRIORITY_COLORS, PRIORITY_LABELS } from '../../constants/task'

interface Props {
  task: Task
  onClick: () => void
  onUpdateFlags: (taskId: string, flags: { needs_detail?: boolean; approved?: boolean }) => void
}

export default function TaskCard({ task, onClick, onUpdateFlags }: Props) {
  const isOverdue = task.due_date && new Date(task.due_date) < new Date() && task.status !== 'done'

  return (
    <div
      onClick={onClick}
      className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-3 cursor-pointer hover:shadow-sm hover:border-indigo-300 dark:hover:border-indigo-600 transition-all"
    >
      <p className="text-sm font-medium text-gray-800 dark:text-gray-100 mb-2 line-clamp-2">{task.title}</p>

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

      <div className="flex items-center justify-between mt-2">
        <span className={clsx('text-xs px-2 py-0.5 rounded-full font-medium', PRIORITY_COLORS[task.priority])}>
          {PRIORITY_LABELS[task.priority]}
        </span>
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
        </div>
      </div>
    </div>
  )
}
